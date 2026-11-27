"""Generate adjacent MP3 or mixed MP3/AAC tracks and their continuous WAV reference."""
import argparse
from array import array
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave

RATE = 44100
# The join falls inside an MPEG granule. The sweep never goes quiet at the join.
CUT = 3 * RATE + 317


def write_wav(path, data):
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes(data)


def make(directory, lame, mixed=False):
    directory.mkdir(parents=True, exist_ok=True)
    tracks = 3 if mixed else 2
    album = "4G mixed gapless test" if mixed else "4G gapless test"
    samples = array("h")
    for i in range(tracks * CUT):
        t = i / RATE
        envelope = min(1, i / 2205, (tracks * CUT - 1 - i) / 2205)
        left = math.sin(math.tau * (137 * t + 36.5 * t * t))
        right = math.sin(math.tau * (223 * t + 55 * t * t) + 0.7)
        samples.extend((round(envelope * 9000 * left), round(envelope * 9000 * right)))
    if sys.byteorder != "little":
        samples.byteswap()
    pcm = samples.tobytes()
    write_wav(directory / "continuous-reference.wav", pcm)
    with TemporaryDirectory() as temp:
        for num in range(1, tracks+1):
            wav = Path(temp) / "part.wav"
            write_wav(wav, pcm[(num-1)*CUT*4:num*CUT*4])
            title = f"Sweep {num}"
            if mixed and num == 2:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-c:a", "aac",
                                "-profile:a", "aac_low", "-b:a", "192k",
                                "-metadata", f"title={title}", "-metadata", "artist=Playback test",
                                "-metadata", f"album={album}", "-metadata", f"track={num}/{tracks}",
                                directory / f"0{num} {title}.m4a"], check=True)
            else:
                subprocess.run([lame, "--silent", "-V", "2", "--tt", title,
                                "--ta", "Playback test", "--tl", album,
                                "--tn", f"{num}/{tracks}", wav, directory / f"0{num} {title}.mp3"],
                               check=True)
    print(f"Generated {tracks} tracks of {CUT} frames each at {RATE} Hz")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--lame", default="lame")
    parser.add_argument("--mixed", action="store_true", help="generate MP3, AAC-LC, MP3 in order")
    args = parser.parse_args()
    make(args.output_dir, args.lame, args.mixed)
