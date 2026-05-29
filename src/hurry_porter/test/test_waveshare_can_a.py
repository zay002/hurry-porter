from hurry_porter.waveshare_can_a import (
    CanFrame,
    decode_fixed_frames,
    decode_variable_frames,
    encode_config,
    encode_fixed_frame,
    encode_variable_frame,
)


def test_encode_variable_standard_frame_matches_waveshare_doc():
    frame = CanFrame(can_id=0x123, data=bytes.fromhex("11 22 33 44 55 66 77 88"))

    assert encode_variable_frame(frame).hex(" ") == "aa c8 23 01 11 22 33 44 55 66 77 88 55"


def test_encode_variable_extended_frame_matches_waveshare_doc():
    frame = CanFrame(can_id=0x01234567, data=bytes.fromhex("11 22 33 44 55 66 77 88"), frame_type="extended")

    assert encode_variable_frame(frame).hex(" ") == "aa e8 67 45 23 01 11 22 33 44 55 66 77 88 55"


def test_encode_variable_remote_frame_uses_remote_bit_and_dlc():
    frame = CanFrame(can_id=0x123, data=b"", frame_format="remote", dlc=4)

    assert encode_variable_frame(frame).hex(" ") == "aa d4 23 01 55"


def test_encode_fixed_frame_checksum_matches_waveshare_doc():
    frame = CanFrame(can_id=0x123, data=bytes.fromhex("11 22 33 44 55 66 77 88"))

    assert encode_fixed_frame(frame).hex(" ") == "aa 55 01 01 01 23 01 00 00 08 11 22 33 44 55 66 77 88 00 93"


def test_encode_config_variable_standard_1m_default():
    assert encode_config(1000000).hex(" ") == "aa 55 12 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 14"


def test_decode_variable_frames_decodes_standard_and_extended():
    stream = bytes.fromhex(
        "aa c2 03 01 11 22 55"
        "00 ff"
        "aa e2 21 30 03 01 11 22 55"
    )

    frames = decode_variable_frames(stream)

    assert [(frame.can_id, frame.frame_type, frame.data.hex(" ")) for frame in frames] == [
        (0x103, "standard", "11 22"),
        (0x01033021, "extended", "11 22"),
    ]


def test_decode_fixed_frame_validates_checksum():
    raw = bytes.fromhex("aa 55 01 01 01 23 01 00 00 08 11 22 33 44 55 66 77 88 00 93")

    frames = decode_fixed_frames(raw)

    assert frames[0].can_id == 0x123
    assert frames[0].data.hex(" ") == "11 22 33 44 55 66 77 88"
    assert frames[0].checksum_ok is True
