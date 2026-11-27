"""Measure a continuous LAME CBR track split, and replay controlled timing changes."""
import argparse
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
from tempfile import TemporaryDirectory
import wave

from elftools.elf.elffile import ELFFile

from make_test_album import RATE, write_wav
from verify_mp3 import Decoder, transmit
from verify_mp3_parity import ReferenceMP3
from verify_pcm_parity import Retail55Pump
from verify_tx import Machine

CUT, SECOND = 13211, 12803
MPEG_FRAME = 1152


def signal():
    # A continuous sweep on the left and isolated, smoothly tapered timing
    # markers on the right. One marker lies on either side of the cut.
    data = bytearray()
    for i in range(CUT + SECOND):
        t = i / RATE
        left = round(10000 * math.sin(math.tau * (316*t + 515*t*t)))
        right = 0
        for center in (CUT-360, CUT+300):
            d = i-center
            if abs(d) < 180:
                right += round(24000 * math.exp(-(d/43)**2) * math.cos(d*0.17))
        data.extend(struct.pack('<hh', left, right))
    return bytes(data)


def encode(directory, name, pcm):
    wav, mp3 = directory / f'{name}.wav', directory / f'{name}.mp3'
    write_wav(wav, pcm)
    subprocess.run(['lame', '--silent', '-b', '128', wav, mp3], check=True)
    return mp3


def tag_timing(data):
    # The fixture is a stereo MPEG-1 stream with all four Xing fields.
    assert data[36:40] == b'Info' and int.from_bytes(data[40:44], 'big') == 15
    frames = int.from_bytes(data[44:48], 'big')
    assert data[156:160] == b'LAME'
    packed = int.from_bytes(data[177:180], 'big')
    delay, padding = packed >> 12, packed & 4095
    return delay, frames*MPEG_FRAME-delay-padding, padding


def apple_packet_table(mp3):
    if not shutil.which('afinfo'):
        return None
    report = subprocess.check_output(['afinfo', mp3], text=True)
    match = re.search(r'audio (\d+) valid frames \+ (\d+) priming \+ (\d+) remainder', report)
    if match:
        count, delay, padding = map(int, match.groups())
        return delay, count, padding
    return None


def four_window(image, elf, data, blocks, timing):
    with elf.open('rb') as file:
        decoder = Decoder(image, ELFFile(file))
        file.seek(0)
        output = Machine(image, file, 0x21f3c)
    state = decoder.origin(data, MPEG_FRAME, tag_timing(data)[1], database_timing=timing)
    return transmit(output, state, blocks)


def five_window(image, blocks, mode, timing):
    delay, count, padding = timing
    return Retail55Pump(image).track(blocks, codec=0, delay=delay, count=count,
                                     padding=padding, mp3_mode=mode)


def readings(pcm, channel):
    return struct.unpack(f'<{len(pcm)//2}h', pcm)[channel::2]


def dbfs(pcm, channel):
    samples = readings(pcm, channel)
    mean_square = sum(x*x for x in samples) / len(samples)
    return 10 * math.log10(mean_square / 32768**2) if mean_square else -math.inf


def verify(target, reference, elf, directory, output):
    pcm = signal()
    joins = [bytearray(), bytearray()]
    for name, part in [('A', pcm[:CUT*4]), ('B', pcm[CUT*4:])]:
        mp3 = encode(directory, name, part)
        data = mp3.read_bytes()
        timing = tag_timing(data)
        delay, count, padding = timing
        assert (delay, count) == (576, len(part)//4)
        apple = apple_packet_table(mp3)
        inspector = ReferenceMP3(reference)
        mode, offset = inspector.file_info(data)
        assert mode == 2 and inspector.first_codec_input(data) == offset == 0
        blocks = ReferenceMP3(reference).decode(data)
        with elf.open('rb') as file:
            assert Decoder(target, ELFFile(file)).decode(data) == blocks
        native = b''.join(blocks)
        four = four_window(target, elf, data, blocks, timing)
        five = five_window(reference, blocks, mode, timing)
        start = 4*(delay+529+MPEG_FRAME)
        assert four == native[start:start+4*count] and len(four) == 4*count
        assert five[:4*MPEG_FRAME] == native[start-4*MPEG_FRAME:start]
        assert five[4*MPEG_FRAME:] == four[:-4*MPEG_FRAME]
        joins[0].extend(four)
        joins[1].extend(five)

        # Change only the four signature bytes. Reuse the original database
        # timing. The diagnostic copy has a stale LAME tag CRC.
        renamed = data[:36] + b'Xing' + data[40:]
        probe = ReferenceMP3(reference)
        renamed_mode, renamed_offset = probe.file_info(renamed)
        assert renamed_mode in (0, 1) and renamed_offset == probe.first_codec_input(renamed) == 0
        assert ReferenceMP3(reference).decode(renamed) == blocks
        assert five_window(reference, blocks, renamed_mode, timing) == four

        # Counterfactual sync record: keep count and padding fixed and
        # include the MPEG header in the database's front delay.
        adjusted = delay+MPEG_FRAME, count, padding
        assert five_window(reference, blocks, mode, adjusted) == four
        adjusted_four = four_window(target, elf, data, blocks, adjusted)
        assert adjusted_four == four, (name, 'header counted twice', len(adjusted_four)//4, count)
        # iTunes 7.0 included the Info frame in the playable count for both tracks.
        legacy = delay, count + MPEG_FRAME, padding
        assert four_window(target, elf, data, blocks, legacy) == four
        print(f'{name}: LAME delay/count/padding {timing}, afinfo {apple}, '
              f'4G with hypothetical delay {adjusted[0]} emitted {len(adjusted_four)//4} frames')
        if output:
            shutil.copyfile(mp3, output / mp3.name)
            if shutil.which('afconvert'):
                rendered = directory / f'{name}-coreaudio.wav'
                subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16', mp3, rendered],
                               check=True)
                with wave.open(str(rendered)) as wav:
                    assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 2, RATE)
                    apple_count = wav.getnframes()
                    apple_right = readings(wav.readframes(apple_count), 1)
                marker = max(range(apple_count), key=lambda i: abs(apple_right[i]))
                print(f'Core Audio {name}: {apple_count} frames, marker {marker}')

    four, five = map(bytes, joins)
    assert len(four) == len(five) == len(pcm)
    right4, right5 = readings(four, 1), readings(five, 1)
    peak4a = max(range(CUT), key=lambda i: abs(right4[i]))
    peak4b = max(range(CUT, len(right4)), key=lambda i: abs(right4[i]))
    peak5b = max(range(CUT, len(right5)), key=lambda i: abs(right5[i]))
    assert abs(peak4a - (CUT-360)) <= 3 and abs(peak4b - (CUT+300)) <= 3
    assert max(map(abs, right5[:CUT])) < max(map(abs, right4[:CUT])) / 100
    assert peak5b-peak4b == MPEG_FRAME
    print(f'Right channel: 4G markers at {peak4a-CUT:+} and {peak4b-CUT:+} frames from the join. '
          f'5.5G pre-join peak {max(map(abs, right5[:CUT]))} vs {max(map(abs, right4[:CUT]))} '
          f'on 4G, post-join marker at {peak5b-CUT:+}.')
    stop = (CUT+round(RATE*.020))*4
    assert dbfs(four[CUT*4:stop], 0) - dbfs(five[CUT*4:stop], 0) > 30
    print(f'Left-channel RMS, first 20 ms after join: source {dbfs(pcm[CUT*4:stop], 0):.1f}, '
          f'4G {dbfs(four[CUT*4:stop], 0):.1f}, 5.5G {dbfs(five[CUT*4:stop], 0):.1f} dBFS.')
    if output:
        for name, samples in [('source', pcm), ('patched-4g', four), ('stock-55g', five)]:
            write_wav(output / f'{name}.wav', samples)

    # Timing captured from macOS 27.3's iPod database writer for these lengths.
    for length, timing in ((24, (576, 1176, 1704)),
                           (1788, (576, 2940, 1092)),
                           (12959, (576, 12959, 2593)),
                           (12960, (576, 14112, 1440)),
                           (12961, (576, 12961, 2591))):
        data = encode(directory, f'finder-{length}', pcm[:length*4]).read_bytes()
        delay, source_length, _ = tag_timing(data)
        assert source_length == length
        with elf.open('rb') as file:
            blocks = Decoder(target, ELFFile(file)).decode(data)
        native = b''.join(blocks)
        start = 4*(MPEG_FRAME + delay + 529)
        actual = four_window(target, elf, data, blocks, timing)
        assert len(actual) == length*4 and actual == native[start:start+length*4], (length, len(actual)//4)
        print(f'Finder count {timing[1]}: 4G emitted exactly {length} source-aligned stereo frames')

    # An actual LAME file the length of the original numbered edge case.
    short = encode(directory, 'short', pcm[:550*4])
    data = short.read_bytes()
    timing = tag_timing(data)
    assert timing[1] == 550 and timing[0]+529+timing[2] >= 550
    blocks = ReferenceMP3(reference).decode(data)
    with elf.open('rb') as file:
        assert Decoder(target, ELFFile(file)).decode(data) == blocks
    ref = ReferenceMP3(reference)
    mode, offset = ref.file_info(data)
    assert mode == 2 and offset == 0
    short4 = four_window(target, elf, data, blocks, timing)
    short5 = five_window(reference, blocks, mode, timing)
    print(f'Actual 550-frame CBR file: tag {timing}, 4G emitted {len(short4)//4}, '
          f'5.5G emitted {len(short5)//4}, byte match {short4 == short5}.')
    assert short4 == short5 == b''.join(blocks)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('stock_4g', 'stock_55g', 'playback_elf'):
        cli.add_argument(name, type=Path)
    cli.add_argument('--output', type=Path, help='save the two MP3s and the three listening WAVs')
    args = cli.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp:
        verify(args.stock_4g.read_bytes(), args.stock_55g.read_bytes(),
               args.playback_elf, Path(temp), args.output)
