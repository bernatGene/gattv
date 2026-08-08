from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from gattv.camera import CameraError, rotate_image
from gattv.capture.models import CapturedUnit, CompletedClip


def encode_clip(
    clip: CompletedClip, output_path: Path, fps: int, rotation: int = 0
) -> None:
    if not clip.units:
        raise CameraError("Could not encode an empty motion clip.")

    first = clip.units[0]
    width, height = (
        (first.height, first.width)
        if rotation in {90, 270}
        else (first.width, first.height)
    )
    output = None
    try:
        output = av.open(str(output_path), mode="w")
        stream = output.add_stream(
            "libx264",
            rate=fps,
            width=width,
            height=height,
            pix_fmt="yuv420p",
            options={"crf": "28", "preset": "veryfast"},
        )
        decoder = (
            av.CodecContext.create("mjpeg", "r") if first.codec == "mjpeg" else None
        )
        for index, unit in enumerate(clip.units):
            frame = _decode_unit(unit, decoder)
            if rotation:
                image = rotate_image(frame.to_ndarray(format="bgr24"), rotation)
                frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame.pts = index
            frame.time_base = Fraction(1, fps)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    except CameraError:
        _discard_output(output, output_path)
        raise
    except (av.FFmpegError, ValueError) as error:
        _discard_output(output, output_path)
        raise CameraError(f"Could not encode motion clip: {error}") from error
    except BaseException:
        _discard_output(output, output_path)
        raise

    try:
        output.close()
    except av.FFmpegError as error:
        output_path.unlink(missing_ok=True)
        raise CameraError(f"Could not finalize motion clip: {error}") from error


def _discard_output(
    output: av.container.OutputContainer | None, output_path: Path
) -> None:
    try:
        if output is not None:
            output.close()
    except av.FFmpegError:
        pass
    output_path.unlink(missing_ok=True)


def _decode_unit(unit: CapturedUnit, decoder: av.CodecContext | None) -> av.VideoFrame:
    if unit.codec == "mjpeg" and decoder is not None:
        frames = decoder.decode(av.Packet(unit.payload))
        if frames:
            return frames[0]
        raise CameraError("Could not decode an MJPEG frame while encoding.")
    if unit.codec == "rawvideo" and unit.pixel_format == "uyvy422":
        return _uyvy_frame(unit)
    raise CameraError(f"Unsupported capture payload: {unit.codec}/{unit.pixel_format}.")


def _uyvy_frame(unit: CapturedUnit) -> av.VideoFrame:
    row_size = unit.width * 2
    expected_size = row_size * unit.height
    if len(unit.payload) != expected_size:
        raise CameraError("Could not encode an unexpected UYVY frame size.")

    frame = av.VideoFrame(unit.width, unit.height, "uyvy422")
    plane = frame.planes[0]
    if plane.line_size == row_size:
        plane.update(unit.payload)
        return frame

    padded = np.zeros(plane.buffer_size, dtype=np.uint8)
    source = np.frombuffer(unit.payload, dtype=np.uint8).reshape(unit.height, row_size)
    target = padded.reshape(unit.height, plane.line_size)
    target[:, :row_size] = source
    plane.update(padded)
    return frame
