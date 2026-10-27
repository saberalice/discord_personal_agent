`speech.wav` contains the first three seconds of the public-domain JFK inaugural
address sample distributed by whisper.cpp:
https://github.com/ggml-org/whisper.cpp/blob/master/samples/jfk.wav

16 kHz mono PCM16. Used to exercise the real WebRTC VAD, separate from tests of
segmentation bookkeeping with deterministic speech decisions.
