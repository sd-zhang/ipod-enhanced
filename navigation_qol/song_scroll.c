/* General Songs title initials, cached while the native importer has the text.
 * Track +ff is the unused byte in the existing 256-byte allocation; native
 * initialization/clone/free already cover it. No wheel-time storage access. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int i32;
#define WORD(p,o) (*(u32 *)((u8 *)(p)+(o)))
#define PTR(p,o) (*(void **)((u8 *)(p)+(o)))
#define FN(a,t) ((t)(a))

void songs_cache_title(const u16 *name,u32 length,u8 *track)
{
    /* A valid host sort title wins even when it precedes display metadata. */
    if (track[0xff]&0x80) return;
    /* Match Albums' immediate ASCII shortcut; byte reads retain the shared
     * parser's alignment contract. Prefixes and Unicode use that parser. */
    const u8 *p=(const u8 *)name;
    u32 first=length ? (p[0]&~32u)|((u32)p[1]<<8) : 0;
    u32 letter=first-'A'<26 ? first :
        FN(0x35c718,u32 (*)(const void *,u32,u32,u32))(name,length*2,1,0);
    track[0xff]=(u8)(letter ? letter : '#');
}

u32 songs_import_sort(void *reader,u8 *track,u32 type,u32 bytes)
{
    u32 (*import)(void *,u8 *,u32,u32)=FN(0x35c5bc,u32 (*)(void *,u8 *,u32,u32));
    (void)type; /* ARM dispatch admits only title-sort metadata27. */
    u8 previous=track[0xff];
    /* The checked album importer touches only its cache byte +fd. Shift
     * that destination to +ff; retain its stream, Unicode and error rules. */
    u32 status=import(reader,track+2,28,bytes);
    u32 letter=track[0xff];
    track[0xff]=(u8)(!status && letter ? letter|0x80 : previous);
    return status;
}

__attribute__((target("arm"),always_inline))
static inline u32 initial(void **index,u32 row)
{
    u8 *track=PTR(index[row],0x20);
    u32 letter=track ? track[0xff]&0x7f : 0;
    return letter ? letter : '#';
}

/* Keep the traversal in ARM: conditional cache loads avoid the Thumb
 * register spills measured in long runs. Validation/import remain Thumb. */
__attribute__((target("arm"),noinline))
static i32 scan(void **index,u32 count,u32 row,i32 direction,u32 *letter)
{
    u32 group=initial(index,row);
    if (direction>0) {
        for (u32 next=row+1;next<count;++next) {
            u32 candidate=initial(index,next);
            if (candidate!=group) { row=next; group=candidate; break; }
        }
    } else if (direction<0) {
        u32 crossed=0;
        while (row) {
            u32 candidate=initial(index,row-1);
            if (candidate!=group) {
                if (crossed) break;
                crossed=1;
                group=candidate;
            }
            --row;
        }
    }
    *letter=group;
    return (i32)row;
}

/* direction=0 reads the current group in constant time. Search only after
 * the fast-mode threshold/rate gate, through the current RAM index. */
i32 songs_fast_target(void *list,u32 row,i32 direction,u32 *letter)
{
    void *model=PTR(list,0x8c);
    if (!model || WORD(model,0)!=0x22fd4) return -1;
    void *ui=PTR(model,0x18);
    if (!ui || WORD(ui,0)!=0x26294) return -1;
    void *query=PTR(ui,0x1c);
    if (!query || WORD(query,0xa74)!=4 || *(u16 *)((u8 *)query+0x670)) return -1;
    u32 count=WORD(query,0xab0);
    void **index=PTR(query,0xaac);
    if (!index || row>=count || count>65535 || WORD(ui,0x1500)!=count ||
        WORD(list,0x9c) || WORD(list,0xa0)!=count) return -1;
    return scan(index,count,row,direction,letter);
}
