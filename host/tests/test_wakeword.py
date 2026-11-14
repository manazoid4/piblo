import numpy as np
import pytest

from mazhost.wakeword import Wakeword


class Stream:
    def accept_waveform(self, rate, samples):
        self.rate, self.samples = rate, samples


class Spotter:
    def __init__(self):
        self.ready = True
        self.resets = 0

    def is_ready(self, stream):
        return self.ready

    def decode_stream(self, stream):
        self.ready = False

    def get_result(self, stream):
        return "HEY_PIBLO"

    def reset_stream(self, stream):
        self.resets += 1


def test_pcm_conversion_and_detection_reset():
    detector = Wakeword.__new__(Wakeword)
    detector.spotter, detector.stream = Spotter(), Stream()
    assert detector.feed(np.array([-32768, 0, 32767], dtype="<i2").tobytes())
    np.testing.assert_allclose(detector.stream.samples, [-1, 0, 32767 / 32768])
    assert detector.stream.rate == 16000
    assert detector.spotter.resets == 1
    assert not detector.feed(b"\0\0")
    with pytest.raises(ValueError):
        detector.feed(b"\0")
    with pytest.raises(ValueError):
        detector.feed(b"\0\0", 8000)
