use anyhow::{Context, Result};
use rusqlite::{params, OptionalExtension};
use std::{
    fmt::Write,
    path::{Path, PathBuf},
};

/// Normalize ASR text, not Discord names. Only the observed standalone credit
/// is flagged; ordinary sentences mentioning subtitles remain untouched.
pub fn normalize(input: &str) -> Result<String> {
    let converter =
        ferrous_opencc::OpenCC::from_config(ferrous_opencc::config::BuiltinConfig::S2tw)?;
    let converted = converter.convert(input);
    let compact: String = converted
        .trim()
        .trim_matches(['(', ')', '（', '）', '[', ']'])
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect::<String>()
        .to_lowercase();
    if matches!(compact.as_str(), "字幕:jchong" | "字幕：jchong") {
        return Ok("[疑似字幕署名誤辨識，已略過]".into());
    }
    Ok(converted)
}

pub fn timestamp(started_ms: i64, offset_ms: u64) -> Result<String> {
    let absolute = started_ms
        .checked_add(i64::try_from(offset_ms)?)
        .context("Transcript timestamp overflow")?;
    let time = chrono::DateTime::from_timestamp_millis(absolute)
        .context("Invalid transcript timestamp")?;
    Ok(time
        .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).expect("UTC+8"))
        .format("%H:%M:%S")
        .to_string())
}

// Only export a completed session. Called after the recorder has flushed its tail.
// Diagnostics remain in SQLite / terminal; the attachment contains speech only.
pub fn export(root: &Path, session: &str, interrupted: bool) -> Result<Option<PathBuf>> {
    let db = crate::recording::open_db(&root.join("log.sqlite3"))?;
    let pending: i64 = db.query_row(
        "SELECT count(*) FROM utterances WHERE session_id=?1 AND status='pending'",
        [session],
        |row| row.get(0),
    )?;
    if pending > 0 && !interrupted {
        return Ok(None);
    }
    let mut query = db.prepare(
        "SELECT u.start_ms,
                COALESCE(NULLIF(TRIM(p.display_name), ''), NULLIF(TRIM(p.username), ''), u.user_id),
                u.text,u.status
         FROM utterances u
         LEFT JOIN users p ON p.session_id=u.session_id AND p.user_id=u.user_id
         WHERE u.session_id=?1 ORDER BY u.start_ms,u.id",
    )?;
    let rows = query.query_map(params![session], |row| {
        Ok((
            row.get::<_, u64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, Option<String>>(2)?,
            row.get::<_, String>(3)?,
        ))
    })?;
    // Older sessions have no join-time clock; their session ID is the best available anchor.
    let started_ms: i64 = db
        .query_row(
            "SELECT started_ms FROM session_clocks WHERE session_id=?1",
            [session],
            |r| r.get(0),
        )
        .optional()?
        .unwrap_or(session.parse()?);
    let mut text = String::new();
    for row in rows {
        let (start, user, result, status) = row?;
        let user = user.split_whitespace().collect::<Vec<_>>().join(" ");
        if status != "done" {
            continue;
        }
        let result = normalize(result.as_deref().unwrap_or_default())?;
        let result = result.split_whitespace().collect::<Vec<_>>().join(" ");
        // Also omit diagnostics persisted by older bot versions.
        if result.is_empty()
            || matches!(
                result.as_str(),
                "[音訊不足 250 ms，未送交辨識]"
                    | "[疑似字幕署名誤辨識，已略過]"
                    | "[未辨識出文字]"
                    | "[ASR 失敗]"
                    | "[ASR 未完成]"
            )
        {
            continue;
        }
        writeln!(
            text,
            "[{}] {}: {}",
            timestamp(started_ms, start)?,
            user,
            result
        )?;
    }
    let started = chrono::DateTime::from_timestamp_millis(session.parse()?)
        .context("Invalid session start time")?;
    let taiwan = chrono::FixedOffset::east_opt(8 * 3600).expect("UTC+8");
    let filename = started
        .with_timezone(&taiwan)
        .format("%Y-%m-%d_%H-%M-%S.txt")
        .to_string();
    let path = root.join(session).join(filename);
    std::fs::write(&path, text)?;
    Ok(Some(path))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn traditional_conversion_and_narrow_credit_filter() -> Result<()> {
        assert_eq!(normalize("对,还是可以一下")?, "對,還是可以一下");
        assert_eq!(
            normalize("然后测试測試，Apex B點")?,
            "然後測試測試，Apex B點"
        );
        assert_eq!(normalize("(字幕:J Chong)")?, "[疑似字幕署名誤辨識，已略過]");
        assert_eq!(
            normalize("（字幕： J Chong ）")?,
            "[疑似字幕署名誤辨識，已略過]"
        );
        assert_eq!(
            normalize("請關掉字幕，J Chong 在右邊")?,
            "請關掉字幕，J Chong 在右邊"
        );
        Ok(())
    }
    #[test]
    fn taiwan_clock_wraps_at_midnight() -> Result<()> {
        let start = chrono::DateTime::parse_from_rfc3339("2026-09-11T23:59:59.200+08:00")?
            .timestamp_millis();
        assert_eq!(timestamp(start, 0)?, "23:59:59");
        assert_eq!(timestamp(start, 800)?, "00:00:00");
        assert_eq!(timestamp(start, 86_400_000)?, "23:59:59");
        Ok(())
    }
    #[test]
    fn export_waits_orders_and_omits_diagnostics() -> Result<()> {
        let root = std::env::temp_dir().join(format!("asr-export-{}", std::process::id()));
        std::fs::create_dir_all(root.join("1789132254200"))?;
        let db = crate::recording::initialize_db(&root.join("log.sqlite3"))?;
        db.execute("INSERT INTO utterances(session_id,user_id,start_ms,end_ms,wav_path,text,status) VALUES ('1789132254200','22',3661000,3662000,'b','推進\n右邊','done'),('1789132254200','11',0,1000,'a',NULL,'pending'),('other','33',0,1000,'c',NULL,'pending')", [])?;
        assert!(export(&root, "1789132254200", false)?.is_none());
        db.execute(
            "UPDATE utterances SET status='error' WHERE user_id='11'",
            [],
        )?;
        let path = export(&root, "1789132254200", false)?.unwrap();
        assert_eq!(path.file_name().unwrap(), "2026-09-11_21-10-54.txt");
        assert_eq!(std::fs::read_to_string(path)?, "[22:11:55] 22: 推進 右邊\n");
        // Server display name wins; a name from another session must not leak in.
        db.execute("INSERT INTO users VALUES ('1789132254200','22','account','隊長'),('other','11','wrong','別場暱稱')", [])?;
        let named = std::fs::read_to_string(export(&root, "1789132254200", false)?.unwrap())?;
        assert!(named.contains("[22:11:55] 隊長: 推進 右邊"));
        assert!(!named.contains("[00:00:00] 11:"));
        db.execute(
            "UPDATE users SET display_name='' WHERE session_id='1789132254200'",
            [],
        )?;
        let fallback = std::fs::read_to_string(export(&root, "1789132254200", false)?.unwrap())?;
        assert!(fallback.contains("[22:11:55] account:"));
        for (i, value) in [
            "[音訊不足 250 ms，未送交辨識]",
            "[未辨識出文字]",
            "[疑似字幕署名誤辨識，已略過]",
            "(字幕:J Chong)",
            "",
            "[ASR 失敗]",
            "[ASR 未完成]",
        ]
        .iter()
        .enumerate()
        {
            db.execute("INSERT INTO utterances(session_id,user_id,start_ms,end_ms,wav_path,text,status) VALUES ('1789132254200','99',0,1000,?1,?2,'done')", params![format!("diagnostic-{i}"), value])?;
        }
        db.execute("INSERT INTO utterances(session_id,user_id,start_ms,end_ms,wav_path,text,status) VALUES ('1789132254200','99',0,1000,'unfinished',NULL,'pending')", [])?;
        let clean = std::fs::read_to_string(export(&root, "1789132254200", true)?.unwrap())?;
        assert_eq!(clean, "[22:11:55] account: 推進 右邊\n");
        db.execute(
            "INSERT INTO session_clocks VALUES ('1789132254200',1789132264200)",
            [],
        )?;
        let anchored = std::fs::read_to_string(export(&root, "1789132254200", true)?.unwrap())?;
        assert_eq!(anchored, "[22:12:05] account: 推進 右邊\n");
        db.execute("DELETE FROM utterances WHERE user_id='22'", [])?;
        assert_eq!(
            std::fs::read_to_string(export(&root, "1789132254200", true)?.unwrap())?,
            ""
        );
        drop(db);
        std::fs::remove_dir_all(root)?;
        Ok(())
    }
}
