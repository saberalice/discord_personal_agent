use crate::vad::Detector;
use anyhow::Result;
use rusqlite::{params, Connection, OptionalExtension};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    time::Duration,
};

const RATE: usize = 16000;
const FRAME: usize = RATE / 50;
const SILENCE_MS: u64 = 1200;
const TAIL_SAMPLES: usize = RATE / 5; // 200 ms of actual received audio after VAD speech.
const MAX_SAMPLES: usize = RATE * 15;

pub enum Input {
    Clock(i64),
    Map(u32, u64),
    Identity(u64, String, String),
    Tick(u64, Vec<(u32, Vec<i16>)>),
    Disconnect(u64),
    Stop,
    #[cfg(test)]
    ClassifiedTick(u64, Vec<(u32, Vec<i16>)>),
}

struct Segment {
    start_ms: u64,
    last_voice_ms: u64,
    samples: Vec<i16>,
    voiced_len: usize,
}

pub struct Store {
    db: Connection,
    directory: PathBuf,
    session: String,
    mappings: HashMap<u32, u64>,
    segments: HashMap<u64, Segment>,
    sequence: u64,
    detectors: HashMap<u64, Detector>,
    pre_roll: HashMap<u64, Vec<i16>>,
}

pub fn open_db(path: &Path) -> Result<Connection> {
    let db = Connection::open(path)?;
    db.busy_timeout(Duration::from_secs(5))?;
    Ok(db)
}

pub fn initialize_db(path: &Path) -> Result<Connection> {
    let db = open_db(path)?;
    db.execute_batch("PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS session_clocks (session_id TEXT PRIMARY KEY, started_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (session_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT, display_name TEXT, PRIMARY KEY(session_id,user_id));
            CREATE TABLE IF NOT EXISTS utterances (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
                start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, wav_path TEXT NOT NULL UNIQUE,
                text TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT);")?;
    Ok(db)
}

pub fn remove_audio(root: &Path, path: &str) -> Result<()> {
    let path = Path::new(path);
    if !path.exists() {
        return Ok(());
    }
    let canonical = path.canonicalize()?;
    anyhow::ensure!(
        canonical.starts_with(root.canonicalize()?)
            && canonical.extension().is_some_and(|ext| ext == "wav"),
        "Refusing to remove audio outside the recording directory"
    );
    std::fs::remove_file(canonical)?;
    Ok(())
}

pub fn next_pending(db: &Connection) -> Result<Option<(i64, String)>> {
    Ok(db
        .query_row(
            "SELECT id,wav_path FROM utterances WHERE status='pending' ORDER BY id LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?)
}

impl Store {
    pub fn new(root: &Path, session: &str, guild: u64, channel: u64) -> Result<Self> {
        let db = initialize_db(&root.join("log.sqlite3"))?;
        db.execute(
            "INSERT INTO sessions VALUES (?1,?2,?3)",
            params![session, guild.to_string(), channel.to_string()],
        )?;
        let directory = root.join(session);
        std::fs::create_dir_all(&directory)?;
        Ok(Self {
            db,
            directory,
            session: session.into(),
            mappings: HashMap::new(),
            segments: HashMap::new(),
            sequence: 0,
            detectors: HashMap::new(),
            pre_roll: HashMap::new(),
        })
    }

    pub fn handle(&mut self, input: Input) -> Result<()> {
        match input {
            Input::Clock(started_ms) => {
                self.db.execute(
                    "INSERT OR REPLACE INTO session_clocks VALUES (?1,?2)",
                    params![self.session, started_ms],
                )?;
            }
            Input::Map(ssrc, user) => {
                if let Some(previous) = self.mappings.insert(ssrc, user) {
                    if previous != user {
                        self.finish_user(previous)?;
                    }
                }
                self.db.execute(
                    "INSERT OR IGNORE INTO users(session_id,user_id) VALUES (?1,?2)",
                    params![self.session, user.to_string()],
                )?;
            }
            Input::Identity(user, username, display) => {
                self.db.execute("INSERT INTO users VALUES (?1,?2,?3,?4) ON CONFLICT(session_id,user_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name", params![self.session, user.to_string(), username, display])?;
            }
            Input::Tick(now, audio) => {
                let mut inputs = HashMap::new();
                for (ssrc, pcm) in audio {
                    if let Some(&user) = self.mappings.get(&ssrc) {
                        inputs.insert(user, pcm);
                    } else {
                        tracing::warn!(ssrc, "Skipping audio without a user mapping");
                    }
                }
                for user in self.detectors.keys() {
                    inputs.entry(*user).or_insert_with(|| vec![0; FRAME]);
                }
                for (user, pcm) in inputs {
                    if self
                        .detectors
                        .get(&user)
                        .is_some_and(|d| now.saturating_sub(d.last_input_ms) >= SILENCE_MS)
                    {
                        self.finish_user(user)?;
                    }
                    if let std::collections::hash_map::Entry::Vacant(entry) =
                        self.detectors.entry(user)
                    {
                        entry.insert(Detector::new()?);
                    }
                    let frames = self.detectors.get_mut(&user).unwrap().push(now, &pcm)?;
                    for frame in frames {
                        self.tick_user(frame.start_ms, user, frame.pcm, frame.speech)?;
                    }
                }
            }
            #[cfg(test)]
            Input::ClassifiedTick(now, audio) => {
                // Deterministic decisions isolate timeline/storage tests from VAD behavior.
                let frames = audio
                    .into_iter()
                    .filter_map(|(ssrc, pcm)| {
                        self.mappings.get(&ssrc).map(|&user| (user, (pcm, true)))
                    })
                    .collect();
                self.tick(now, frames)?;
            }
            Input::Disconnect(user) => {
                self.finish_user(user)?;
                self.mappings.retain(|_, mapped| *mapped != user);
            }
            Input::Stop => {}
        }
        Ok(())
    }

    #[cfg(test)]
    fn tick(&mut self, now: u64, mut frames: HashMap<u64, (Vec<i16>, bool)>) -> Result<()> {
        for user in self.segments.keys() {
            frames
                .entry(*user)
                .or_insert_with(|| (vec![0; FRAME], false));
        }
        for (user, (pcm, speech)) in frames {
            self.tick_user(now, user, pcm, speech)?;
        }
        Ok(())
    }

    fn tick_user(&mut self, now: u64, user: u64, pcm: Vec<i16>, speech: bool) -> Result<()> {
        if self
            .segments
            .get(&user)
            .is_some_and(|s| now.saturating_sub(s.last_voice_ms) >= SILENCE_MS)
        {
            self.flush(user)?;
        }
        if speech && !self.segments.contains_key(&user) {
            let samples = self.pre_roll.remove(&user).unwrap_or_default();
            self.segments.insert(
                user,
                Segment {
                    start_ms: now.saturating_sub((samples.len() / 16) as u64),
                    last_voice_ms: now,
                    voiced_len: 0,
                    samples,
                },
            );
        }
        if let Some(segment) = self.segments.get_mut(&user) {
            segment.samples.extend_from_slice(&pcm);
            if speech {
                segment.last_voice_ms = now;
                segment.voiced_len = segment.samples.len();
            }
            if segment.samples.len() >= MAX_SAMPLES {
                self.flush(user)?;
            }
        } else {
            // Keep 200 ms before activation so VAD onset latency doesn't clip words.
            let recent = self.pre_roll.entry(user).or_default();
            recent.extend_from_slice(&pcm);
            if recent.len() > 3200 {
                recent.drain(..recent.len() - 3200);
            }
        }
        Ok(())
    }

    fn finish_user(&mut self, user: u64) -> Result<()> {
        if let Some(mut detector) = self.detectors.remove(&user) {
            if let Some(frame) = detector.finish()? {
                self.tick_user(frame.start_ms, user, frame.pcm, frame.speech)?;
                // Preserve a partial final window of an already active utterance.
                if let Some(segment) = self.segments.get_mut(&user) {
                    segment.voiced_len = segment.samples.len();
                }
            }
        }
        self.flush(user)?;
        self.pre_roll.remove(&user);
        Ok(())
    }

    fn flush(&mut self, user: u64) -> Result<()> {
        let Some(mut segment) = self.segments.remove(&user) else {
            return Ok(());
        };
        segment.samples.truncate(segment.voiced_len + TAIL_SAMPLES);
        if segment.samples.is_empty() {
            return Ok(());
        }
        self.sequence += 1;
        let path = self
            .directory
            .join(format!("{user}-{:06}.wav", self.sequence));
        let mut wav = hound::WavWriter::create(
            &path,
            hound::WavSpec {
                channels: 1,
                sample_rate: RATE as u32,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )?;
        for sample in &segment.samples {
            wav.write_sample(*sample)?;
        }
        wav.finalize()?;
        let end = segment.start_ms + (segment.samples.len() * 1000 / RATE) as u64;
        self.db.execute("INSERT INTO utterances(session_id,user_id,start_ms,end_ms,wav_path) VALUES (?1,?2,?3,?4,?5)", params![self.session, user.to_string(), segment.start_ms, end, path.to_string_lossy()])?;
        tracing::info!(
            user,
            start_ms = segment.start_ms,
            end_ms = end,
            "Saved temporary audio"
        );
        Ok(())
    }

    pub fn finish(&mut self) -> Result<()> {
        for user in self.detectors.keys().copied().collect::<Vec<_>>() {
            self.finish_user(user)?;
        }
        for user in self.segments.keys().copied().collect::<Vec<_>>() {
            self.flush(user)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    fn store() -> (PathBuf, Store) {
        let root = std::env::temp_dir().join(format!(
            "asr-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&root).unwrap();
        let store = Store::new(&root, "test", 1, 2).unwrap();
        (root, store)
    }

    #[test]
    fn brief_pause_stays_in_sentence_and_quiet_tail_survives() -> Result<()> {
        let (root, mut store) = store();
        store.tick_user(0, 100, vec![1000; FRAME], true)?;
        for time in (20..1000).step_by(20) {
            store.tick_user(time, 100, vec![0; FRAME], false)?;
        }
        store.tick_user(1000, 100, vec![2000; FRAME], true)?;
        for time in (1020..=2200).step_by(20) {
            // Quiet trailing sound rejected by VAD must still reach the recognizer.
            store.tick_user(time, 100, vec![100; FRAME], false)?;
        }
        let count: i64 = store
            .db
            .query_row("SELECT count(*) FROM utterances", [], |r| r.get(0))?;
        assert_eq!(count, 1);
        let (path, end): (String, u64) =
            store
                .db
                .query_row("SELECT wav_path,end_ms FROM utterances", [], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })?;
        assert_eq!(end, 1220);
        let samples = hound::WavReader::open(path)?
            .into_samples::<i16>()
            .collect::<std::result::Result<Vec<_>, _>>()?;
        assert_eq!(
            &samples[samples.len() - TAIL_SAMPLES..],
            vec![100; TAIL_SAMPLES]
        );
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn audio_cleanup_keeps_text_and_rejects_outside_files() -> Result<()> {
        let (root, store) = store();
        let audio = root.join("test.wav");
        let text = root.join("log.txt");
        std::fs::write(&audio, b"test")?;
        std::fs::write(&text, b"transcript")?;
        remove_audio(&root, audio.to_str().unwrap())?;
        assert!(!audio.exists());
        assert!(remove_audio(&root, text.to_str().unwrap()).is_err());
        let outside = root.with_extension("wav");
        std::fs::write(&outside, b"outside")?;
        assert!(remove_audio(&root, outside.to_str().unwrap()).is_err());
        assert!(outside.exists());
        std::fs::remove_file(outside)?;
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn real_vad_rejects_silence_and_splits_two_speech_bursts() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        store.handle(Input::Map(20, 200))?;
        let pcm = hound::WavReader::new(std::io::Cursor::new(include_bytes!(
            "../tests/fixtures/speech.wav"
        )))?
        .into_samples::<i16>()
        .collect::<std::result::Result<Vec<_>, _>>()?;
        for tick in 0..550_u64 {
            let now = tick * 20;
            let offset = if (1000..4000).contains(&now) {
                Some(now - 1000)
            } else if (6000..9000).contains(&now) {
                Some(now - 6000)
            } else {
                None
            };
            let frame = offset
                .map(|ms| pcm[ms as usize * 16..ms as usize * 16 + FRAME].to_vec())
                .unwrap_or_else(|| vec![0; FRAME]);
            store.handle(Input::Tick(now, vec![(10, frame), (20, vec![0; FRAME])]))?;
        }
        store.finish()?;
        let silent: i64 = store.db.query_row(
            "SELECT count(*) FROM utterances WHERE user_id='200'",
            [],
            |r| r.get(0),
        )?;
        assert_eq!(silent, 0);
        let starts: Vec<u64> = store
            .db
            .prepare("SELECT start_ms FROM utterances WHERE user_id='100' ORDER BY start_ms")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        assert!(
            starts.iter().any(|t| (1000..4000).contains(t)),
            "first speech burst: {starts:?}"
        );
        assert!(
            starts.iter().any(|t| (6000..9000).contains(t)),
            "second speech burst: {starts:?}"
        );
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn silero_stop_keeps_partial_final_window_without_padding() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        let pcm = hound::WavReader::new(std::io::Cursor::new(include_bytes!(
            "../tests/fixtures/speech.wav"
        )))?
        .into_samples::<i16>()
        .collect::<std::result::Result<Vec<_>, _>>()?;
        for (i, frame) in pcm.chunks(FRAME).enumerate() {
            store.handle(Input::Tick(i as u64 * 20, vec![(10, frame.to_vec())]))?;
        }
        store.finish()?;
        let end: u64 = store
            .db
            .query_row("SELECT max(end_ms) FROM utterances", [], |r| r.get(0))?;
        assert_eq!(end, (pcm.len() / 16) as u64);
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn overlapping_users_and_internal_silence_keep_separate_audio() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        store.handle(Input::Map(20, 200))?;
        store.handle(Input::Identity(100, "alice".into(), "Alice".into()))?;
        store.handle(Input::ClassifiedTick(
            0,
            vec![(10, vec![1000; FRAME]), (20, vec![-2000; FRAME])],
        ))?;
        store.handle(Input::ClassifiedTick(20, vec![]))?;
        store.handle(Input::ClassifiedTick(40, vec![(10, vec![3000; FRAME])]))?;
        for time in (60..=1240).step_by(20) {
            store.handle(Input::ClassifiedTick(time, vec![]))?;
        }
        let count: i64 = store
            .db
            .query_row("SELECT count(*) FROM utterances", [], |r| r.get(0))?;
        assert_eq!(count, 2);
        let (path, start, end): (String, u64, u64) = store.db.query_row(
            "SELECT wav_path,start_ms,end_ms FROM utterances WHERE user_id='100'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        let samples = hound::WavReader::open(path)?
            .into_samples::<i16>()
            .collect::<std::result::Result<Vec<_>, _>>()?;
        assert_eq!((start, end), (0, 260));
        assert_eq!(
            samples,
            [
                vec![1000; FRAME],
                vec![0; FRAME],
                vec![3000; FRAME],
                vec![0; TAIL_SAMPLES]
            ]
            .concat()
        );
        let other: String = store.db.query_row(
            "SELECT wav_path FROM utterances WHERE user_id='200'",
            [],
            |r| r.get(0),
        )?;
        assert_eq!(
            hound::WavReader::open(other)?
                .into_samples::<i16>()
                .collect::<std::result::Result<Vec<_>, _>>()?,
            [vec![-2000; FRAME], vec![0; TAIL_SAMPLES]].concat()
        );
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn disconnect_flushes_and_ssrc_reuse_does_not_misattribute() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        store.handle(Input::ClassifiedTick(0, vec![(10, vec![1000; FRAME])]))?;
        store.handle(Input::Disconnect(100))?;
        store.handle(Input::ClassifiedTick(20, vec![(10, vec![2000; FRAME])]))?; // unknown: skip
        store.handle(Input::Map(10, 200))?;
        store.handle(Input::ClassifiedTick(40, vec![(10, vec![3000; FRAME])]))?;
        store.finish()?;
        let rows: i64 = store
            .db
            .query_row("SELECT count(*) FROM utterances", [], |r| r.get(0))?;
        assert_eq!(rows, 2);
        assert!(next_pending(&store.db)?.is_some());
        drop(store);
        let db = open_db(&root.join("log.sqlite3"))?;
        assert!(next_pending(&db)?.is_some()); // durable queue survives restart
        drop(db);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn new_recording_session_does_not_reuse_old_speaker_mapping() -> Result<()> {
        let (root, mut first) = store();
        first.handle(Input::Map(10, 100))?;
        first.handle(Input::ClassifiedTick(0, vec![(10, vec![1000; FRAME])]))?;
        first.finish()?;
        drop(first);
        let mut second = Store::new(&root, "second", 3, 4)?;
        second.handle(Input::ClassifiedTick(0, vec![(10, vec![2000; FRAME])]))?;
        second.handle(Input::Map(10, 200))?;
        second.handle(Input::ClassifiedTick(20, vec![(10, vec![3000; FRAME])]))?;
        second.finish()?;
        let count: i64 = second.db.query_row(
            "SELECT count(*) FROM utterances WHERE session_id='second' AND user_id='200' AND start_ms=20",
            [], |r| r.get(0),
        )?;
        assert_eq!(count, 1);
        let total: i64 = second
            .db
            .query_row("SELECT count(*) FROM utterances", [], |r| r.get(0))?;
        assert_eq!(total, 2);
        drop(second);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn reception_gap_does_not_join_distant_utterances() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        store.handle(Input::ClassifiedTick(0, vec![(10, vec![1000; FRAME])]))?;
        store.handle(Input::ClassifiedTick(5000, vec![(10, vec![2000; FRAME])]))?;
        store.finish()?;
        let starts: Vec<u64> = store
            .db
            .prepare("SELECT start_ms FROM utterances ORDER BY id")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        assert_eq!(starts, vec![0, 5000]);
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn continuous_audio_is_bounded_and_shutdown_saves_tail() -> Result<()> {
        let (root, mut store) = store();
        store.handle(Input::Map(10, 100))?;
        for tick in 0..751 {
            store.handle(Input::ClassifiedTick(
                tick * 20,
                vec![(10, vec![1000; FRAME])],
            ))?;
        }
        store.finish()?;
        let durations: Vec<u64> = store
            .db
            .prepare("SELECT end_ms-start_ms FROM utterances ORDER BY id")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        assert_eq!(durations, vec![15000, 20]);
        drop(store);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }
}
