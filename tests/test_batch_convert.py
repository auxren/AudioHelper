"""Tests for the batch-convert ffmpeg command builder."""
from pathlib import Path

from audiohelper.batch_convert import _build_cmd, ConversionPreset, OUTPUT_EXT


def _args(preset):
    return _build_cmd(Path("ffmpeg"), Path("in.wav"), Path("out"), preset)


def test_flac_compression_level():
    a = _args(ConversionPreset("p", fmt="FLAC", flac_level=8))
    assert "-c:a" in a and "flac" in a
    assert "-compression_level" in a and "8" in a


def test_mp3_vbr():
    a = _args(ConversionPreset("p", fmt="MP3", mp3_mode="VBR", mp3_vbr_q=0))
    assert "libmp3lame" in a and "-q:a" in a and "0" in a


def test_mp3_cbr():
    a = _args(ConversionPreset("p", fmt="MP3", mp3_mode="CBR", mp3_cbr_br=320))
    assert "-b:a" in a and "320k" in a


def test_wav_bit_depth():
    a = _args(ConversionPreset("p", fmt="WAV", wav_bits=24))
    assert "pcm_s24le" in a


def test_aac_bitrate():
    a = _args(ConversionPreset("p", fmt="AAC", aac_br=256))
    assert "aac" in a and "256k" in a


def test_ogg_quality():
    a = _args(ConversionPreset("p", fmt="OGG", ogg_q=6.0))
    assert "libvorbis" in a


def test_output_extensions():
    assert OUTPUT_EXT["FLAC"] == ".flac"
    assert OUTPUT_EXT["AAC"] == ".m4a"
    assert OUTPUT_EXT["OGG"] == ".ogg"
