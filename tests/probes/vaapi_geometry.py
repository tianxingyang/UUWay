#!/usr/bin/python3
"""Qualify FFmpeg VA-API geometry using synthetic frames, never a desktop.

This is a host-driver preflight, not the UU/Wine ABI or remote-client test.
Temporary elementary streams are deleted after decoding their metadata.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def check(device, codec, width, height):
    with tempfile.TemporaryDirectory(prefix='uuway-vaapi-geometry-') as temporary:
        stream = Path(temporary) / ('synthetic.' + codec)
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-vaapi_device', str(device),
                   '-f', 'lavfi', '-i', f'testsrc2=size={width}x{height}:rate=10',
                   '-vf', 'format=nv12,hwupload', '-frames:v', '3', '-c:v', codec + '_vaapi',
                   '-bf', '0', '-rc_mode', 'CBR', '-b:v', '5M', '-async_depth', '1',
                   '-flags', '+global_header', '-bsf:v', 'dump_extra', '-f', codec, str(stream)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_frames', '-show_entries',
            'frame=width,height', '-of', 'json', str(stream)], check=True,
            capture_output=True, text=True, timeout=30)
        frames = json.loads(result.stdout).get('frames', [])
        dimensions = sorted({(frame['width'], frame['height']) for frame in frames})
        return dict(codec=codec, requested=[width, height], decoded=dimensions,
                    frames=len(frames), passed=len(frames) == 3 and dimensions == [(width, height)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', type=Path, required=True, help='Explicit authorized DRM render node')
    parser.add_argument('--codec', choices=('h264', 'hevc', 'all'), default='h264')
    arguments = parser.parse_args()
    failed = False
    for codec in ('h264', 'hevc') if arguments.codec == 'all' else (arguments.codec,):
        for width, height in ((1280, 720), (720, 1280)):
            try:
                report = check(arguments.device, codec, width, height)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
                report = dict(codec=codec, requested=[width, height], passed=False,
                              error_type=type(error).__name__)
            print(json.dumps(report, sort_keys=True), flush=True)
            failed |= not report['passed']
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
