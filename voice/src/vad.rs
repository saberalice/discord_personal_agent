use anyhow::Result;
use silero_vad_rust::silero_vad::model::OnnxModel;

const WINDOW: usize = 512; // Silero at 16 kHz: contiguous 32 ms windows.

pub struct Frame {
    pub start_ms: u64,
    pub pcm: Vec<i16>,
    pub speech: bool,
}

pub struct Detector {
    model: OnnxModel,
    pending: Vec<i16>,
    origin_ms: u64,
    consumed: u64,
    pub last_input_ms: u64,
    started: bool,
}

impl Detector {
    pub fn new() -> Result<Self> {
        Ok(Self {
            model: OnnxModel::from_path(
                std::env::var("VAD_MODEL").unwrap_or_else(|_| "models/silero_vad.onnx".into()),
                true,
            )?,
            pending: Vec::new(),
            origin_ms: 0,
            consumed: 0,
            last_input_ms: 0,
            started: false,
        })
    }

    pub fn push(&mut self, now: u64, pcm: &[i16]) -> Result<Vec<Frame>> {
        if !self.started {
            self.origin_ms = now;
            self.started = true;
        }
        self.last_input_ms = now;
        self.pending.extend_from_slice(pcm);
        let mut frames = Vec::new();
        while self.pending.len() >= WINDOW {
            let pcm = self.pending.drain(..WINDOW).collect();
            frames.push(self.classify(pcm)?);
        }
        Ok(frames)
    }

    fn classify(&mut self, pcm: Vec<i16>) -> Result<Frame> {
        let mut input = [0.0; WINDOW];
        for (dst, src) in input.iter_mut().zip(&pcm) {
            *dst = *src as f32 / 32768.0;
        }
        let speech = self.model.forward_chunk(&input, 16000)?[[0, 0]] >= 0.5;
        let start_ms = self.origin_ms + self.consumed / 16;
        self.consumed += pcm.len() as u64;
        Ok(Frame {
            start_ms,
            pcm,
            speech,
        })
    }

    pub fn finish(&mut self) -> Result<Option<Frame>> {
        if self.pending.is_empty() {
            return Ok(None);
        }
        // Pad only the model input. The saved tail keeps its original length.
        let pcm = std::mem::take(&mut self.pending);
        Ok(Some(self.classify(pcm)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reblocking_preserves_samples_timestamps_and_tail() -> Result<()> {
        let mut vad = Detector::new()?;
        let mut frames = Vec::new();
        for i in 0..7 {
            frames.extend(vad.push(1000 + i * 20, &[0; 320])?);
        }
        frames.extend(vad.finish()?);
        assert_eq!(frames.iter().map(|f| f.pcm.len()).sum::<usize>(), 2240);
        assert_eq!(
            frames.iter().map(|f| f.start_ms).collect::<Vec<_>>(),
            vec![1000, 1032, 1064, 1096, 1128]
        );
        assert!(frames.iter().all(|f| !f.speech));
        assert!(vad.finish()?.is_none());
        Ok(())
    }
}
