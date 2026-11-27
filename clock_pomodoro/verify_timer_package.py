"""Independently validate the complete r35 timer-recovery layer."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
from elftools.elf.elffile import ELFFile
from verify_pattern_package import (ROOT,MEMBER,ZIP_FIELDS,ARTIFACT_PATTERNS,
    read_archive,payload,repacked_stock,artifacts,word,branch_target)

BASE=ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r34.ipsw'
BASE_SHA='44899cf29ba0dd2e6fe035b33f737c27aec5e8fd04132998934480488af78b3a'
TEXT_SHA='e3b55dbf69818565197680d3a00c332a2c545d8e89c94c54ba6ebc03ec618898'
ALLOWED={0xd94b0,0xd94b4,0xd94b8,0xe8,0x2e8,0x4a8}


def validated_parent(candidate):
    sys.path.insert(0,str(ROOT/'gapless'))
    from verify_release_package import validated_parent as validate_cleanup
    candidate=Path(candidate)
    if (candidate.with_suffix('.artifacts')/'stock_performance.elf').exists():
        from verify_performance_package import validated_parent as validate_performance
        return validated_parent(validate_performance(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest()==BASE_SHA,'r34 source pin'
    validate_cleanup(BASE)
    old_infos,old,old_comment=read_archive(BASE)
    infos,new,comment=read_archive(candidate)
    assert list(old)==list(new) and comment==old_comment,'ZIP members/comment'
    for left,right in zip(old_infos,infos):
        assert all(getattr(left,f)==getattr(right,f) for f in ZIP_FIELDS),'ZIP metadata'
    assert all(old[n]==new[n] for n in old if n!=MEMBER),'unrelated member'
    before,after=payload(old[MEMBER]),payload(new[MEMBER])
    stock=read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert new[MEMBER]==repacked_stock(stock,after),'stock repack/checksum'
    assert payload(old[MEMBER],0x4228)==payload(new[MEMBER],0x4228),'updater unchanged'
    assert before[-4:]==after[-4:]==bytes.fromhex('55aa55aa'),'end marker'
    old_art=artifacts(BASE.with_suffix('.artifacts'))
    art=candidate.with_suffix('.artifacts')
    new_art=artifacts(art)
    assert set(new_art)==set(old_art)|{'timer_recovery.elf','timer-recovery-symbols.json','timer-recovery-edits.json'},'artifact set'
    for name,raw in old_art.items(): assert new_art[name]==raw,('carried artifact changed',name)
    assert (art/'diagnostics-cleanup.json').read_bytes()==(BASE.with_suffix('.artifacts')/'diagnostics-cleanup.json').read_bytes(),'cleanup metadata'
    elf=ELFFile(io.BytesIO(new_art['timer_recovery.elf']))
    code=elf.get_section_by_name('.text')
    base,size=len(before)-4,code['sh_size']
    assert elf['e_machine']=='EM_ARM' and elf.elfclass==32 and elf.little_endian,'ARM ELF'
    assert code['sh_addr']==base and code['sh_flags']==6 and 0<size<=1024,'code layout'
    assert len(after)==len(before)+size<=0x390000,'image bounds'
    assert code.data()==after[base:-4],'ELF/image correspondence'
    assert hashlib.sha256(code.data()).hexdigest()==TEXT_SHA,'reviewed timer ARM contract'
    assert {s.name for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']}=={'.text'},'added state'
    assert not any(s['sh_size'] for s in elf.iter_sections() if s['sh_type'] in ('SHT_REL','SHT_RELA')),'relocations'
    syms={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    funcs={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
           if s['st_info']['type']=='STT_FUNC' and s['st_shndx']!='SHN_UNDEF'}
    owned=json.loads(old_art['pomodoro-symbols.json'])
    for name in ('pomo_ui_timer','pomo_ui_visible','pomo_state'):
        assert syms[name]==owned[name],('owned state binding',name)
    assert elf['e_entry']==syms['pomo_timer_deliver'],'ELF entry'
    assert json.loads(new_art['timer-recovery-symbols.json'])==funcs,'symbol metadata'
    edits=json.loads(new_art['timer-recovery-edits.json'])
    assert len(edits)==6 and set(edits)==ALLOWED,'edit metadata'
    assert branch_target(after,0xd94b0)==syms['pomo_timer_deliver'],'worker hook'
    assert word(after,0xd94b4)==word(after,0xd94b8)==0xe1a00000,'worker sequence'
    assert word(after,0xe8)==len(after),'image length'
    for at in (0x2e8,0x4a8): assert word(after,at)==0x04000000+len(after)-4,'image end'
    restored=bytearray(after[:base])
    for at in ALLOWED: restored[at:at+4]=before[at:at+4]
    assert restored==before[:-4],'unrelated image bytes'
    build=json.loads((art/'build.json').read_text())
    assert build==dict(source_sha256=BASE_SHA,sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        osos_sha256=hashlib.sha256(after).hexdigest(),osos_bytes=len(after),headroom=0x390000-len(after),
        timer_code_bytes=size,added_state_bytes=0,flashed=False),'build metadata'
    return BASE


class CorruptionChecks(unittest.TestCase):
    def test_rejects_worker_code_state_and_preservation_corruption(self):
        infos,members,comment=read_archive(self.candidate)
        original=payload(members[MEMBER]); art=self.candidate.with_suffix('.artifacts')
        raw=(art/'timer_recovery.elf').read_bytes()
        code=ELFFile(io.BytesIO(raw)).get_section_by_name('.text'); base=code['sh_addr']
        stock=read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
        cases=[('hook',0xd94b0,'worker hook'),('duplicate cleanup',0xd94b8,'worker sequence'),
               ('heap',0x4b0,'unrelated image bytes'),('playback',0x38d480,'unrelated image bytes'),
               ('LCD',0xc3f7c,'unrelated image bytes'),('code',base,'ELF/image'),
               ('matching ELF',base,'reviewed timer ARM contract'),('carried ELF',None,'carried artifact')]
        with TemporaryDirectory(prefix='timer-package-negative-') as temporary:
            for i,(name,at,failure) in enumerate(cases):
                with self.subTest(name=name):
                    path=Path(temporary)/f'bad-{i}.ipsw'; target=path.with_suffix('.artifacts'); target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS,'build.json','diagnostics-cleanup.json'):
                        for source in art.glob(pattern): shutil.copyfile(source,target/source.name)
                    image=bytearray(original)
                    if at is not None: image[at]^=1
                    if name=='matching ELF':
                        changed=bytearray(raw); changed[code['sh_offset']]^=1
                        (target/'timer_recovery.elf').write_bytes(changed)
                    if name=='carried ELF':
                        changed=bytearray((target/'lcd.elf').read_bytes()); changed[-1]^=1
                        (target/'lcd.elf').write_bytes(changed)
                    new=dict(members); new[MEMBER]=repacked_stock(stock,image)
                    with ZipFile(path,'w') as archive:
                        archive.comment=comment
                        for info in infos: archive.writestr(copy.copy(info),new[info.filename])
                    with self.assertRaisesRegex(AssertionError,failure): validated_parent(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate',type=Path); parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args(); validated_parent(args.candidate)
    print('Timer recovery package: exact worker/overlay contract and carried bytes preserved: PASS')
    if args.self_test:
        target=args.candidate
        if (target.with_suffix('.artifacts')/'stock_performance.elf').exists():
            from verify_performance_package import validated_parent as validate_performance
            target=validate_performance(target)
        CorruptionChecks.candidate=target
        unittest.main(argv=[__file__],verbosity=2)
