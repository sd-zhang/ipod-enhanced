"""Stage gapless metadata in each 4G music playback request before submission."""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from struct import pack_into

IMPORT_SHA256 = "75fce17ecf7778c8f14ea12b9d597eceba1b1252e24c9c9414865dcaed6ae69d"

# In the music request constructor's allocation block, delay calculation of
# stack argument 7 until just before it is needed. That frees four instructions
# to copy the track extension while r5 still owns its database pointer.
REQUEST_AT = 0xa7d9c
REQUEST_WORDS = (
    0xe1580006,  # cmp r8,r6
    0x81a08006,  # movhi r8,r6
    0xe3a00fb3,  # mov r0,#716             ; legacy request 700 plus 16 bytes
    0xebffcece,  # bl 0x9b8e8
    0xe1b04000,  # movs r4,r0
    0x03e0606b,  # mvneq r6,#107
    0x0a00004c,  # beq 0xa7eec             ; retain allocation error path
    0xe28500ec,  # add r0,r5,#0xec         ; four imported track words
    0xe890000f,  # ldm r0,{r0-r3}
    0xe284cfaf,  # add r12,r4,#700
    0xe88c000f,  # stm r12,{r0-r3}         ; owned by this specific request
    0xe59d3030,  # ldr r3,[sp,#0x30]
    0xe3130004,  # tst r3,#4
    0x03a03001,  # moveq r3,#1
    0x13a03000,  # movne r3,#0
    0xe59d0244,  # ldr r0,[sp,#0x244]
    0xe584029c,  # str r0,[r4,#0x29c]
    0xe2842f9b,  # add r2,r4,#620
    0xe5d50011,  # ldrb r0,[r5,#0x11]
    0xe3100004,  # tst r0,#4
    0x159f1108,  # ldrne r1,[pc,#0x108]    ; legacy literal 30000
    0xe28d0014,  # add r0,sp,#20
    0x03a01000,  # moveq r1,#0
)


def patch_requests(import_image: bytes) -> bytes:
    if sha256(import_image).hexdigest() != IMPORT_SHA256:
        raise ValueError("expected the 4.3.1.1 metadata-import image")
    image = bytearray(import_image)
    pack_into(f"<{len(REQUEST_WORDS)}I", image, REQUEST_AT, *REQUEST_WORDS)
    return bytes(image)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("input_osos", type=Path)
    parser.add_argument("output_osos", type=Path)
    args = parser.parse_args()
    patched = patch_requests(args.input_osos.read_bytes())
    with args.output_osos.open("xb") as output:
        output.write(patched)
    print(f"{args.output_osos}  SHA-256 {sha256(patched).hexdigest()}")
