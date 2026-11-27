"""Extend the native script resource without renumbering stock functions/globals."""
import struct
from image import resources


def make_record(atom: int, code: bytes, *, parameters=0, locals=0) -> bytes:
    if not 0<=atom<0xffff or not 0<=parameters<=0xffff or not 0<=locals<=0xffff:
        raise ValueError('invalid script header')
    # Native ARM7 trailer is a halfword; keep the declared code length even.
    code=code+bytes(len(code)%2)
    if len(code)>0xffff:raise ValueError('script too large')
    return struct.pack('<5H',atom,parameters,locals,6,len(code))+code+b'\0\0'


def merge_scripts(image: bytes, updates: dict[int,bytes]) -> bytes:
    at,size=resources(image)['TCmd'][0x2380]
    old=image[at:at+size]
    if not updates:return old
    head=8+4*struct.unpack_from('<H',old,6)[0]
    count,explicit=struct.unpack_from('<HH',old,head)
    ids=old[head+4:head+4+2*explicit]
    directory=(head+4+2*explicit+3)&~3
    offsets=struct.unpack_from(f'<{count+1}I',old,directory)
    body=directory+4*(count+1)
    if offsets[0]!=0 or body+offsets[-1]>len(old):raise ValueError('invalid script bounds')
    records=[];seen=set()
    for start,end in zip(offsets,offsets[1:]):
        record=old[body+start:body+end]
        atom=struct.unpack_from('<H',record)[0]
        if atom in seen:raise ValueError('duplicate original function')
        records.append((atom,updates.get(atom,record)));seen.add(atom)
    additions=sorted(set(updates)-seen)
    # Original implicit/explicit-ID record order remains unchanged. All new
    # functions join the explicit table; gaps in their IDs are intentional.
    records.extend((atom,updates[atom]) for atom in additions)
    for atom,record in records:
        if len(record)<12 or len(record)%2 or struct.unpack_from('<H',record)[0]!=atom:
            raise ValueError('invalid function record identity or alignment')
        length=struct.unpack_from('<H',record,8)[0]
        if length%2 or 12+length>len(record):raise ValueError('invalid function trailer')
    header=bytearray(old[:head])
    maximum=max(struct.unpack_from('<H',old)[0],max(updates)+1)
    if maximum>0xffff:raise ValueError('function ID overflow')
    struct.pack_into('<H',header,0,maximum)
    header.extend(struct.pack('<HH',len(records),explicit+len(additions)))
    header.extend(ids+b''.join(struct.pack('<H',atom) for atom in additions))
    header.extend(bytes(-len(header)%4))
    positions=[0];payload=bytearray()
    for _,record in records:payload.extend(record);positions.append(len(payload))
    header.extend(struct.pack(f'<{len(positions)}I',*positions))
    return bytes(header+payload+old[body+offsets[-1]:])
