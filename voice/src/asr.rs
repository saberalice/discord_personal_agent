use anyhow::{Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::{
    net::TcpListener,
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

pub struct Asr {
    child: Child,
    http: ureq::Agent,
    url: String,
    key: String,
    language: String,
}

impl Asr {
    pub fn load(path: &str) -> Result<Self> {
        let engine = std::env::var("ASR_ENGINE").unwrap_or_else(|_| {
            if cfg!(windows) {
                "vendor/llama.cpp/build/bin/Release/llama-server.exe"
            } else {
                "vendor/llama.cpp/build/bin/llama-server"
            }
            .into()
        });
        let projector = std::env::var("ASR_MMPROJ")
            .unwrap_or_else(|_| "models/qwen3-asr-gguf/mmproj-Qwen3-ASR-1.7B-bf16.gguf".into());
        let language = std::env::var("ASR_LANGUAGE").unwrap_or_else(|_| "Chinese".into());
        let language = match language.as_str() {
            "Chinese" | "chinese" | "zh" | "zh-TW" | "zh-CN" => "Chinese",
            "English" | "english" | "en" => "English",
            "auto" => "",
            _ => anyhow::bail!("ASR_LANGUAGE must be Chinese, English or auto"),
        }
        .to_owned();
        anyhow::ensure!(
            std::path::Path::new(path).is_file(),
            "Missing GGUF; run python3 scripts/download-qwen.py"
        );
        anyhow::ensure!(
            std::path::Path::new(&projector).is_file(),
            "Missing audio projector: {projector}"
        );
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port().to_string();
        drop(listener);
        let key = uuid::Uuid::new_v4().to_string();
        let layers = std::env::var("ASR_GPU_LAYERS").unwrap_or_else(|_| "99".into());
        let mut command = Command::new(engine);
        command
            .args([
                "-m",
                path,
                "--mmproj",
                &projector,
                "--host",
                "127.0.0.1",
                "--port",
                &port,
                "--api-key",
                &key,
                "-ngl",
                &layers,
                "-c",
                "4096",
                "-np",
                "1",
                "--no-warmup",
            ])
            .env_remove("DISCORD_BOT_TOKEN")
            .env("LLAMA_MEDIA_MARKER", "<__media__>")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit());
        if layers == "0" {
            command.arg("--no-mmproj-offload");
        }
        // Isolate Ctrl+C so the parent can finish queued ASR before shutting the server down.
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x00000200); // CREATE_NEW_PROCESS_GROUP
        }
        let child = command
            .spawn()
            .context("Start llama-server; run scripts/build-engine.py first")?;
        let http: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(300)))
            .http_status_as_error(false)
            .build()
            .into();
        let mut model = Self {
            child,
            http,
            url: format!("http://127.0.0.1:{port}"),
            key,
            language,
        };
        let deadline = Instant::now() + Duration::from_secs(300);
        loop {
            anyhow::ensure!(
                model.child.try_wait()?.is_none(),
                "llama-server exited during startup"
            );
            if model
                .http
                .get(format!("{}/health", model.url))
                .call()
                .is_ok_and(|response| response.status().is_success())
            {
                let models: Value = model
                    .http
                    .get(format!("{}/v1/models", model.url))
                    .header("Authorization", format!("Bearer {}", model.key))
                    .call()?
                    .body_mut()
                    .read_json()?;
                anyhow::ensure!(
                    models.to_string().contains("multimodal"),
                    "llama-server lacks multimodal support"
                );
                break;
            }
            anyhow::ensure!(Instant::now() < deadline, "llama-server startup timeout");
            std::thread::sleep(Duration::from_millis(200));
        }
        Ok(model)
    }

    pub fn transcribe(&mut self, path: &str) -> Result<String> {
        let reader = hound::WavReader::open(path)?;
        let spec = reader.spec();
        anyhow::ensure!(
            spec.channels == 1
                && spec.sample_rate == 16000
                && spec.bits_per_sample == 16
                && spec.sample_format == hound::SampleFormat::Int,
            "Expected 16 kHz mono PCM16 WAV"
        );
        if reader.duration() < 4000 {
            tracing::info!(path, "Skipping audio shorter than 250 ms");
            return Ok(String::new());
        }
        anyhow::ensure!(
            self.child.try_wait()?.is_none(),
            "llama-server is no longer running"
        );
        let prefix = if self.language.is_empty() {
            String::new()
        } else {
            format!("language {}<asr_text>", self.language)
        };
        // mtmd expands the media marker into audio_start + embeddings + audio_end.
        let prompt = format!("<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<__media__><|im_end|>\n<|im_start|>assistant\n{prefix}");
        let reply: Value = self.http.post(format!("{}/completion", self.url))
            .header("Authorization", format!("Bearer {}", self.key))
            .send_json(json!({"prompt": {"prompt_string": prompt, "multimodal_data": [STANDARD.encode(normalized_wav(path)?)]},
                "temperature": 0, "n_predict": 1024, "cache_prompt": false, "stream": false,
                "stop": ["<|im_end|>", "<|endoftext|>"]}))?
            .body_mut().read_json()?;
        anyhow::ensure!(reply["stop_type"] != "limit", "ASR output hit token limit");
        anyhow::ensure!(reply.get("error").is_none(), "llama-server: {reply}");
        let text = reply["content"].as_str().context("Missing ASR content")?;
        let text = text.split_once("<asr_text>").map_or(text, |(_, text)| text);
        crate::transcript::normalize(text.trim())
    }
}

impl Drop for Asr {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn volume_adjustment_preserves_normal_audio_and_bounds_boost() {
        let mut normal = vec![3000i16; 320];
        assert_eq!(normalize_volume(&mut normal), 1.0);
        assert_eq!(normal, vec![3000; 320]);
        let mut quiet = vec![200i16; 320];
        let gain = normalize_volume(&mut quiet);
        assert!(gain > 1.0 && gain <= 10f64.powf(12.0 / 20.0));
        let mut with_pause = [vec![200i16; 320], vec![0; 16000]].concat();
        assert_eq!(normalize_volume(&mut with_pause), gain);
        assert_eq!(&with_pause[..320], &quiet);
        let mut silence = vec![1i16; 320];
        assert_eq!(normalize_volume(&mut silence), 1.0);
        let mut loud = vec![20000i16; 320];
        assert!(normalize_volume(&mut loud) < 1.0);
        let mut transient = vec![200i16; 3200];
        transient[0] = i16::MIN;
        normalize_volume(&mut transient);
        assert!(transient.iter().all(|&v| (v as f64).abs() <= 29205.0));
    }

    #[test]
    #[ignore = "Requires downloaded GGUF, built llama-server, and local GPU access"]
    fn real_engine_reuses_model_and_skips_short_audio() -> Result<()> {
        let mut model = Asr::load("models/qwen3-asr-gguf/Qwen3-ASR-1.7B-bf16.gguf")?;
        let wav =
            std::env::var("ASR_TEST_WAV").unwrap_or_else(|_| "tests/fixtures/speech.wav".into());
        for _ in 0..2 {
            let start = Instant::now();
            let text = model.transcribe(&wav)?;
            assert!(!text.is_empty());
            assert!(!text.contains("<asr_text>"));
            eprintln!("ASR {:.2}s: {text}", start.elapsed().as_secs_f64());
        }
        let path = std::env::temp_dir().join(format!("asr-short-{}.wav", uuid::Uuid::new_v4()));
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: 16000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::create(&path, spec)?;
        for _ in 0..1600 {
            writer.write_sample(0i16)?;
        }
        writer.finalize()?;
        let result = model.transcribe(path.to_str().unwrap());
        std::fs::remove_file(path)?;
        assert_eq!(result?, "");
        Ok(())
    }
}

/// Adjust only unusually quiet/loud utterances; exclude near-silent 20 ms frames
/// from the level estimate. A single constant gain preserves within-utterance dynamics.
fn normalize_volume(pcm: &mut [i16]) -> f64 {
    let mut energy = 0.0;
    let mut count = 0;
    let mut peak = 0.0f64;
    for frame in pcm.chunks(320) {
        let sum: f64 = frame.iter().map(|&v| (v as f64 / 32768.0).powi(2)).sum();
        // -50 dBFS: do not boost near-silence to speech level.
        if sum / frame.len() as f64 >= 1e-5 {
            energy += sum;
            count += frame.len();
        }
        for &v in frame {
            peak = peak.max((v as f64 / 32768.0).abs());
        }
    }
    if count == 0 {
        return 1.0;
    }
    let rms = (energy / count as f64).sqrt();
    let db = 20.0 * rms.log10();
    if (-30.0..=-16.0).contains(&db) {
        return 1.0;
    }
    let gain = (10f64.powf(-23.0 / 20.0) / rms)
        .clamp(10f64.powf(-12.0 / 20.0), 10f64.powf(12.0 / 20.0))
        .min(10f64.powf(-1.0 / 20.0) / peak);
    for sample in pcm {
        *sample = (*sample as f64 * gain).round().clamp(-32768.0, 32767.0) as i16;
    }
    gain
}

fn normalized_wav(path: &str) -> Result<Vec<u8>> {
    let reader = hound::WavReader::open(path)?;
    let spec = reader.spec();
    let mut pcm = reader
        .into_samples::<i16>()
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let gain = normalize_volume(&mut pcm);
    if (gain - 1.0).abs() > 0.001 {
        tracing::info!(gain_db = 20.0 * gain.log10(), "Adjusted ASR input volume");
    }
    let mut bytes = std::io::Cursor::new(Vec::new());
    {
        let mut writer = hound::WavWriter::new(&mut bytes, spec)?;
        for sample in pcm {
            writer.write_sample(sample)?;
        }
        writer.finalize()?;
    }
    Ok(bytes.into_inner())
}
