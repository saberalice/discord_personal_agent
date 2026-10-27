mod asr;
mod recording;
mod transcript;
mod vad;

use anyhow::{Context as _, Result};
use asr::Asr;
use recording::{Input, Store};
use serenity::{
    all::{
        ChannelId, ChannelType, Command, CommandInteraction, CreateAttachment, CreateCommand,
        CreateMessage, EditInteractionResponse, GatewayIntents, GuildId, Interaction,
        InteractionContext, Ready,
    },
    async_trait,
    client::{Context, EventHandler},
    http::Http,
};
use songbird::{
    driver::{Channels, DecodeConfig, DecodeMode, SampleRate},
    CoreEvent, Event, EventContext, SerenityInit,
};
use std::{
    env,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, OnceLock,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{mpsc, Mutex};

#[derive(Clone)]
struct Receiver {
    tx: mpsc::Sender<Input>,
    failed: Arc<AtomicBool>,
    http: Arc<Http>,
    guild: GuildId,
    start: Arc<OnceLock<Instant>>,
    accepting: Arc<AtomicBool>,
}

impl Receiver {
    fn send(&self, input: Input) {
        if !self.accepting.load(Ordering::SeqCst) {
            return;
        }
        if let Err(error) = self.tx.try_send(input) {
            tracing::error!(%error, "Recording queue unavailable; stopping to avoid silent audio loss");
            self.failed.store(true, Ordering::SeqCst);
        }
    }
}

#[async_trait]
impl songbird::EventHandler for Receiver {
    async fn act(&self, event: &EventContext<'_>) -> Option<Event> {
        match event {
            EventContext::SpeakingStateUpdate(speaking) => {
                if let Some(id) = speaking.user_id {
                    let user = id.0;
                    self.send(Input::Map(speaking.ssrc, user));
                    let receiver = self.clone();
                    tokio::spawn(async move {
                        match receiver.guild.member(&receiver.http, user).await {
                            Ok(member) => {
                                let display = member.display_name().to_owned();
                                let _ = receiver
                                    .tx
                                    .send(Input::Identity(user, member.user.name, display))
                                    .await;
                            }
                            Err(error) => {
                                tracing::warn!(user, %error, "Could not resolve username")
                            }
                        }
                    });
                }
            }
            EventContext::VoiceTick(tick) => {
                let audio = tick
                    .speaking
                    .iter()
                    .filter_map(|(ssrc, data)| {
                        data.decoded_voice.as_ref().map(|pcm| (*ssrc, pcm.clone()))
                    })
                    .collect();
                if let Some(start) = self.start.get() {
                    self.send(Input::Tick(start.elapsed().as_millis() as u64, audio));
                }
            }
            EventContext::ClientDisconnect(client) => {
                self.send(Input::Disconnect(client.user_id.0))
            }
            _ => {}
        }
        None
    }
}

struct Active {
    session: String,
    guild: GuildId,
    channel: ChannelId,
    tx: mpsc::Sender<Input>,
    accepting: Arc<AtomicBool>,
    recorder: tokio::task::JoinHandle<Result<()>>,
}

impl Active {
    async fn stop(self, manager: &songbird::Songbird) -> Result<()> {
        self.accepting.store(false, Ordering::SeqCst);
        let removed = manager.remove(self.guild).await;
        let _ = self.tx.send(Input::Stop).await;
        self.recorder.await.context("Recorder task panicked")??;
        removed.context("Could not leave voice channel")?;
        Ok(())
    }
}

struct Reply {
    text: String,
    attachment: Option<PathBuf>,
}
impl From<String> for Reply {
    fn from(text: String) -> Self {
        Self {
            text,
            attachment: None,
        }
    }
}
impl From<&str> for Reply {
    fn from(text: &str) -> Self {
        text.to_owned().into()
    }
}

struct Handler {
    root: PathBuf,
    failed: Arc<AtomicBool>,
    active: Arc<Mutex<Option<Active>>>,
    shutting_down: Arc<AtomicBool>,
}

impl Handler {
    async fn command(&self, ctx: &Context, command: &CommandInteraction) -> Result<Reply> {
        let Some(guild) = command.guild_id else {
            return Ok("請在伺服器中使用這個指令。".into());
        };
        let channel = ctx.cache.guild(guild).and_then(|g| {
            g.voice_states
                .get(&command.user.id)
                .and_then(|v| v.channel_id)
        });
        let Some(channel) = channel else {
            return Ok("請先加入要錄音的語音頻道，再使用指令。".into());
        };
        // Serialize join/leave so two commands cannot create overlapping recording sessions.
        let mut active = self.active.lock().await;
        if self.shutting_down.load(Ordering::SeqCst) {
            return Ok("Bot 正在關閉。".into());
        }
        let manager = songbird::get(ctx).await.context("Songbird unavailable")?;
        if command.data.name == "leave" {
            let Some(current) = active.as_ref() else {
                return Ok("目前沒有錄音。".into());
            };
            if current.guild != guild || current.channel != channel {
                return Ok("請在 bot 正在錄音的語音頻道內使用 /leave。".into());
            }
            let current = active.take().expect("checked above");
            let session = current.session.clone();
            current.stop(&manager).await?;
            drop(active);
            // Keep the interaction acknowledged while the final ASR jobs finish.
            let _ = command
                .edit_response(
                    &ctx.http,
                    EditInteractionResponse::new()
                        .content("已停止錄音，正在完成辨識並整理文字檔……"),
                )
                .await;
            loop {
                let interrupted =
                    self.failed.load(Ordering::SeqCst) || self.shutting_down.load(Ordering::SeqCst);
                let root = self.root.clone();
                let session = session.clone();
                let file = tokio::task::spawn_blocking(move || {
                    transcript::export(&root, &session, interrupted)
                })
                .await??;
                if let Some(path) = file {
                    if tokio::fs::metadata(&path).await?.len() == 0 {
                        return Ok("已停止錄音，本場沒有可匯出的辨識文字。".into());
                    }
                    return Ok(Reply {
                        text: "本場語音紀錄已完成，請下載文字檔。".into(),
                        attachment: Some(path),
                    });
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
        }
        if active.is_some() {
            return Ok("Bot 已在錄音，請先於原頻道使用 /leave。".into());
        }
        let is_voice = ctx
            .cache
            .guild(guild)
            .and_then(|g| {
                g.channels
                    .get(&channel)
                    .map(|c| c.kind == ChannelType::Voice)
            })
            .unwrap_or(false);
        if !is_voice {
            return Ok("目前只支援一般語音頻道。".into());
        }
        let session = SystemTime::now()
            .duration_since(UNIX_EPOCH)?
            .as_millis()
            .to_string();
        let root = self.root.clone();
        let recorder_session = session.clone();
        let (tx, mut rx) = mpsc::channel(256);
        let accepting = Arc::new(AtomicBool::new(true));
        let start = Arc::new(OnceLock::new());
        let receiver = Receiver {
            tx: tx.clone(),
            failed: self.failed.clone(),
            http: ctx.http.clone(),
            guild,
            start: start.clone(),
            accepting: accepting.clone(),
        };
        let failed = self.failed.clone();
        let recorder = tokio::task::spawn_blocking(move || {
            let result = (|| -> Result<()> {
                let mut store = Store::new(&root, &recorder_session, guild.get(), channel.get())?;
                while let Some(input) = rx.blocking_recv() {
                    if matches!(input, Input::Stop) {
                        break;
                    }
                    store.handle(input)?;
                }
                store.finish()
            })();
            if let Err(error) = &result {
                tracing::error!(%error, "Recorder failed");
                failed.store(true, Ordering::SeqCst);
            }
            result
        });
        let current = Active {
            session,
            guild,
            channel,
            tx,
            accepting,
            recorder,
        };
        {
            let call = manager.get_or_insert(guild);
            let mut call = call.lock().await;
            for event in [
                CoreEvent::SpeakingStateUpdate,
                CoreEvent::VoiceTick,
                CoreEvent::ClientDisconnect,
            ] {
                call.add_global_event(event.into(), receiver.clone());
            }
        }
        if let Err(error) = manager.join(guild, channel).await {
            let _ = current.stop(&manager).await;
            return Err(error.into());
        }
        let origin = Instant::now();
        let wall_ms = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as i64;
        if current.tx.send(Input::Clock(wall_ms)).await.is_err() {
            let _ = current.stop(&manager).await;
            anyhow::bail!("Recorder stopped before recording started");
        }
        let _ = start.set(origin);
        *active = Some(current);
        Ok(format!(
            "🔴 已加入 <#{}> 並開始錄音。使用 /leave 停止。",
            channel.get()
        )
        .into())
    }
}

#[async_trait]
impl EventHandler for Handler {
    async fn ready(&self, ctx: Context, ready: Ready) {
        for (name, description) in [
            ("join", "加入你所在的語音頻道並開始錄音"),
            ("leave", "停止錄音並離開語音頻道"),
        ] {
            let builder = CreateCommand::new(name)
                .description(description)
                .contexts(vec![InteractionContext::Guild]);
            if let Err(error) = Command::create_global_command(&ctx.http, builder).await {
                tracing::error!(%error, "Could not register slash commands");
                self.failed.store(true, Ordering::SeqCst);
                return;
            }
        }
        tracing::info!(bot = %ready.user.name, version = env!("CARGO_PKG_VERSION"), "Ready: /join records; /leave uploads TXT");
    }

    async fn interaction_create(&self, ctx: Context, interaction: Interaction) {
        let Interaction::Command(command) = interaction else {
            return;
        };
        if !matches!(command.data.name.as_str(), "join" | "leave") {
            return;
        }
        if let Err(error) = command.defer(&ctx.http).await {
            tracing::warn!(%error, "Could not acknowledge command");
            return;
        }
        let response_started = Instant::now();
        let message = match self.command(&ctx, &command).await {
            Ok(message) => message,
            Err(error) => {
                tracing::error!(%error, "Voice command failed");
                "操作失敗，請確認 bot 能查看、連接該頻道，並查看本機紀錄。".into()
            }
        };
        let mut response = EditInteractionResponse::new().content(&message.text);
        if let Some(path) = &message.attachment {
            match CreateAttachment::path(path).await {
                Ok(file) => {
                    response = response.new_attachment(file);
                }
                Err(error) => {
                    tracing::error!(%error, "Cannot read transcript attachment");
                    let _ = command
                        .edit_response(
                            &ctx.http,
                            EditInteractionResponse::new()
                                .content("文字檔已存到本機，但讀取附件失敗。請查看本機紀錄。"),
                        )
                        .await;
                    return;
                }
            }
        }
        // Interaction tokens expire after 15 minutes. A long ASR backlog can use
        // a regular channel upload instead; the text file is always retained locally.
        if response_started.elapsed() < Duration::from_secs(14 * 60) {
            match command.edit_response(&ctx.http, response).await {
                Ok(_) => return,
                Err(error) => {
                    tracing::warn!(%error, "Interaction upload failed; trying channel upload")
                }
            }
        }
        if let Some(path) = message.attachment {
            let result = async {
                let file = CreateAttachment::path(&path).await?;
                command
                    .channel_id
                    .send_message(
                        &ctx.http,
                        CreateMessage::new().content(message.text).add_file(file),
                    )
                    .await?;
                Ok::<_, serenity::Error>(())
            }
            .await;
            if let Err(error) = result {
                tracing::error!(%error, file = %path.display(), "Transcript retained locally; Discord upload failed");
                let _ = command.edit_response(&ctx.http, EditInteractionResponse::new()
                    .content("文字檔已存到本機，但上傳失敗。請確認 bot 在此頻道有傳送訊息與附加檔案權限。")).await;
            }
        }
    }
}

fn required(name: &str) -> Result<String> {
    let value = env::var(name).with_context(|| format!("Set {name} in .env"))?;
    anyhow::ensure!(!value.trim().is_empty(), "{name} is empty");
    Ok(value)
}

#[tokio::main]
async fn main() -> Result<()> {
    if env::args().nth(1).as_deref() == Some("--version") {
        println!(
            "discord-asr-bot {} (Silero VAD + Qwen3-ASR 1.7B + TXT upload + temporary audio)",
            env!("CARGO_PKG_VERSION")
        );
        return Ok(());
    }
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "discord_asr_bot=info,songbird=warn".into()),
        )
        .init();
    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        "Starting bot: Silero VAD + Qwen3-ASR 1.7B; audio is temporary"
    );
    let model_path = env::var("ASR_MODEL")
        .unwrap_or_else(|_| "models/qwen3-asr-gguf/Qwen3-ASR-1.7B-bf16.gguf".into());
    let mut model = Asr::load(&model_path)?;
    tracing::info!(model = %model_path, "Qwen3-ASR 1.7B ready (llama.cpp, GGUF BF16); Silero VAD");
    // Offline smoke test uses the same inference path, without connecting to Discord.
    if let Some(wav) = env::args().nth(1) {
        println!("{}", model.transcribe(&wav)?);
        return Ok(());
    }
    // Fail before joining Discord if the local VAD model cannot load.
    drop(vad::Detector::new().context("Load local Silero VAD")?);
    let token = required("DISCORD_BOT_TOKEN")?;
    let root = PathBuf::from(env::var("DATA_DIR").unwrap_or_else(|_| "data".into()));
    std::fs::create_dir_all(&root)?;
    let root = root.canonicalize()?;
    recording::initialize_db(&root.join("log.sqlite3"))?;
    let failed = Arc::new(AtomicBool::new(false));
    let active = Arc::new(Mutex::new(None::<Active>));
    let shutting_down = Arc::new(AtomicBool::new(false));
    let config = songbird::Config::default().decode_mode(DecodeMode::Decode(DecodeConfig::new(
        Channels::Mono,
        SampleRate::Hz16000,
    )));
    let mut client = serenity::Client::builder(
        &token,
        GatewayIntents::GUILDS | GatewayIntents::GUILD_VOICE_STATES,
    )
    .event_handler(Handler {
        root: root.clone(),
        failed: failed.clone(),
        active: active.clone(),
        shutting_down: shutting_down.clone(),
    })
    .register_songbird_from_config(config)
    .await?;
    let manager = client
        .data
        .read()
        .await
        .get::<songbird::serenity::SongbirdKey>()
        .expect("Songbird registered")
        .clone();
    let shards = client.shard_manager.clone();
    let done = Arc::new(AtomicBool::new(false));
    let asr_done = done.clone();
    let asr = tokio::task::spawn_blocking(move || -> Result<()> {
        let db = recording::open_db(&root.join("log.sqlite3"))?;
        loop {
            // Read before querying so shutdown cannot race the final insertion.
            let stopping = asr_done.load(Ordering::SeqCst);
            if let Some((id, path)) = recording::next_pending(&db)? {
                match model.transcribe(&path) {
                    Ok(text) => {
                        db.execute(
                            "UPDATE utterances SET text=?1, status='done' WHERE id=?2",
                            rusqlite::params![text, id],
                        )?;
                        // Transcript text stays out of logs; only its size is recorded.
                        tracing::info!(id, chars = text.chars().count(), "Transcribed");
                    }
                    Err(error) => {
                        db.execute(
                            "UPDATE utterances SET status='error', error=?1 WHERE id=?2",
                            rusqlite::params![error.to_string(), id],
                        )?;
                        tracing::error!(id, %error, "ASR failed; error recorded");
                    }
                }
                // Only remove audio after the transcript/error is committed to SQLite.
                recording::remove_audio(&root, &path)?;
            } else if stopping {
                break;
            } else {
                std::thread::sleep(Duration::from_millis(250));
            }
        }
        Ok(())
    });
    let mut gateway = tokio::spawn(async move { client.start().await });
    let result: Result<()> = tokio::select! {
        signal = tokio::signal::ctrl_c() => signal.context("Listen for Ctrl+C"),
        result = &mut gateway => match result {
            Ok(result) => result.context("Discord gateway failed"),
            Err(error) => Err(error.into()),
        },
        _ = async {
            while !failed.load(Ordering::SeqCst) && !asr.is_finished() {
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
        } => Err(anyhow::anyhow!("Recording or ASR worker stopped; inspect logs")),
    };
    shutting_down.store(true, Ordering::SeqCst);
    let recorded = if let Some(current) = active.lock().await.take() {
        current.stop(&manager).await
    } else {
        Ok(())
    };
    shards.shutdown_all().await;
    gateway.abort();
    done.store(true, Ordering::SeqCst);
    tracing::info!("WAV recording stopped; finishing queued ASR");
    let transcribed = asr.await.context("ASR task panicked");
    recorded?;
    transcribed??;
    result
}
