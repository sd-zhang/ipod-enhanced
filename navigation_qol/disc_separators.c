/* Monochrome4G native album headings. The native Songs index owns its footer;
 * query invalidation frees both together. Native song indexes never change.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int i32;
#define WORD(p,o) (*(u32 *)((u8 *)(p)+(o)))
#define HALF(p,o) (*(u16 *)((u8 *)(p)+(o)))
#define PTR(p,o) (*(void **)((u8 *)(p)+(o)))
#define FN(a,t) ((t)(a))
#define MAGIC 0x44495343u
#define MAX_GROUPS 256u
struct groups { u32 magic, count, first[MAX_GROUPS]; };

extern void discs_header_draw(void *,const i32 *,const i32 *,u32);

void *discs_index_alloc(u32 bytes)
{
    return FN(0x9b8e0,void *(*)(u32))(bytes+sizeof(struct groups));
}

void discs_index_finish(void *query,void **index,u32 count)
{
    struct groups *g=(struct groups *)(index+count);
    g->magic=MAGIC;
    g->count=0;
    if (!count || count>65535 || !HALF(query,0x670) || HALF(query,0x670)>255)
        return;
    u32 prior=0,groups=0,multiple=0;
    for (u32 i=0;i<count;++i) {
        void *track=PTR(index[i],0x20);
        if (!track || !HALF(track,0x88)) return;
        u32 disc=HALF(track,0x88);
        if (disc>1 || HALF(track,0x8a)>1) multiple=1;
        if (disc!=prior) {
            if (groups==MAX_GROUPS) return;
            g->first[groups++]=i;
            prior=disc;
        }
    }
    if (multiple || groups>1) g->count=groups;
}

static struct groups *ui_groups(void *ui)
{
    if (!ui || WORD(ui,0)!=0x26294) return 0;
    void *query=PTR(ui,0x1c);
    if (!query || !HALF(query,0x670) || HALF(query,0x670)>255) return 0;
    u32 n=WORD(query,0xab0);
    void **index=PTR(query,0xaac);
    if (!index || !n || n>65535 || WORD(ui,0x1500)!=n) return 0;
    struct groups *g=(struct groups *)(index+n);
    return g->magic==MAGIC && g->count && g->count<=MAX_GROUPS ? g : 0;
}

static struct groups *list_groups(void *list,void **ui)
{
    if (!list || WORD(list,0)!=0x29030 || HALF(list,0x1c)!=0x3301) return 0;
    void *model=PTR(list,0x8c);
    if (!model || WORD(model,0)!=0x22fd4) return 0;
    *ui=PTR(model,0x18);
    struct groups *g=ui_groups(*ui);
    return g && WORD(list,0x9c)==0 && WORD(list,0xa0)==WORD(*ui,0x1500)+g->count ? g : 0;
}

/* Upper bound among header positions first[i]+i. Header maps to its following
 * song for metadata; the header flag prevents its use as a selectable row. */
static u32 song_row(struct groups *g,u32 row,u32 *header)
{
    u32 lo=0,hi=g->count;
    while (lo<hi) {
        u32 mid=lo+(hi-lo)/2;
        if (g->first[mid]+mid<=row) lo=mid+1; else hi=mid;
    }
    *header=lo && g->first[lo-1]+lo-1==row;
    return *header ? g->first[lo-1] : row-lo;
}

u32 discs_visible_count(void *ui,u32 property)
{
    u32 n=FN(0x7679c,u32 (*)(void *))(ui);
    if (property!=0x3301) return n;
    struct groups *g=ui_groups(ui);
    return n+(g ? g->count : 0);
}

u32 discs_model_property(void *model,u32 tag,u32 property,u32 *out)
{
    void *ui;
    if (property==0x3367 || property==0x3368) {
        /* Native model+1c owns the list. Scope mapping to that exact3301
         * instance, including when other screens retain an album query. */
        struct groups *g=list_groups(PTR(model,0x1c),&ui);
        u32 row=WORD(model,0x14);
        if (g && row<WORD(ui,0x1500)+g->count) {
            u32 header;
            row=song_row(g,row,&header);
            *out=header ? (property==0x3367 ? (u32)"" : 0) :
                FN(0x6f648,u32 (*)(void *,u32,u32,u32))(ui,tag,property,row);
            return 1;
        }
    }
    return FN(0x7315c,u32 (*)(void *,u32,u32,u32 *))(model,tag,property,out);
}

void discs_select(void *list,i32 row,u32 notification)
{
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (g && row>=0 && (u32)row<WORD(list,0xa0)) {
        u32 header;
        song_row(g,(u32)row,&header);
        if (header) {
            i32 old=FN(0x96fc4,i32 (*)(void *))(list);
            /* Previous from the first song reaches header0 before Apple's
             * normal lower-bound wrap. Honor the list's native wrap policy. */
            if (!row && old>0 && (i32)WORD(list,0xa4)<0 &&
                !(WORD(list,0x20)&0x04000000u))
                row=(i32)WORD(list,0xa0)-1;
            else
                row+=row>0 && old>row ? -1 : 1;
        }
    }
    FN(0xd1100,void (*)(void *,i32,u32))(list,row,notification);
}

i32 discs_selected_song(void *list)
{
    i32 row=FN(0x96fc4,i32 (*)(void *))(list);
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (g && row>=0 && (u32)row<WORD(list,0xa0)) {
        u32 header;
        u32 song=song_row(g,(u32)row,&header);
        return header ? -1 : (i32)song;
    }
    return row;
}

/* Hold Center's script sends the logical AA result back to Renderer.AF.
 * Blink the corresponding displayed song, preserving the native timer/state. */
void discs_flash_song(void *renderer,i32 row)
{
    void *ui;
    struct groups *g=list_groups(PTR(renderer,0x24),&ui);
    if (g && row>=0 && (u32)row<WORD(ui,0x1500)) {
        u32 lo=0,hi=g->count;
        while (lo<hi) {
            u32 mid=lo+(hi-lo)/2;
            if (g->first[mid]<=(u32)row) lo=mid+1; else hi=mid;
        }
        row+=(i32)lo;
    }
    FN(0x66938,void (*)(void *,i32))(renderer,row);
}

void discs_draw_row(void *list,void *model,const i32 *clip,u32 row,
                    const i32 *rect,u32 highlight,u32 alternate)
{
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (g && row<WORD(list,0xa0)) {
        u32 header;
        u32 song=song_row(g,row,&header);
        if (header) {
            void **index=PTR(PTR(ui,0x1c),0xaac);
            discs_header_draw(list,clip,rect,HALF(PTR(index[song],0x20),0x88));
            return;
        }
    }
    FN(0x59974,void (*)(void *,void *,const i32 *,u32,const i32 *,u32,u32))
        (list,model,clip,row,rect,highlight,alternate);
}

/* Native previous-row code must exclude header0 before it calculates movement
 * and boundary feedback. The stored lower bound stays0 for list geometry. */
u32 discs_previous_min(void *list)
{
    void *ui;
    return list_groups(list,&ui) ? 1 : WORD(list,0x9c);
}

/* Apple's reveal request covers only the selected song. Include its heading
 * before native viewport clamping, so scrolling up cannot strand the heading
 * immediately above the visible area. Active disc jumps request a viewport
 * from the heading; Apple's parent reveal still owns the bottom clamp. */
void discs_reveal_rect(void *list,i32 *rect)
{
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (!g) return;
    i32 row=FN(0x96fc4,i32 (*)(void *))(list);
    if (row<=0 || (u32)row>=WORD(list,0xa0)) return;
    u32 header;
    song_row(g,(u32)row-1,&header);
    if (!header) return;
    i32 height=rect[2]-rect[0];
    rect[0]-=height;
    void *state=PTR(list,0xe4),*parent=PTR(list,0x10);
    if (!state || WORD(state,44)!=0x4c545234u || !WORD(state,92) ||
        !parent || WORD(parent,0)!=0x22770 || !(WORD(parent,0x20)&2)) return;
    i32 viewport=(i32)WORD(parent,0x60)-(i32)WORD(parent,0x58);
    if (height<=0 || viewport>128 || height>viewport/2) return;
    /* Native bfeb0 adds the leftover partial row after this hook. The 4G
     * division helper takes denominator first, numerator second. */
    rect[2]=rect[0]+FN(0x348,i32 (*)(i32,i32))(height,viewport)*height;
}

/* Fast scrolling uses the same index-owned runs. Return visual first-song
 * positions; a single partial disc does not enable a disc-jump mode. */
i32 discs_fast_target(void *list,u32 row,i32 direction,u32 *number)
{
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (!g || g->count<2 || !row || row>=WORD(list,0xa0)) return -1;
    u32 lo=0,hi=g->count;
    while (lo<hi) {
        u32 mid=lo+(hi-lo)/2;
        if (g->first[mid]+mid<row) lo=mid+1; else hi=mid;
    }
    u32 group=lo-1,first=g->first[group]+group+1;
    if (direction>0) {
        if (group+1<g->count) ++group;
    } else if (direction<0 && row<=first && group) --group;
    /* The checked target run also supplies its caption. Keep the result on
     * the caller's stack; drawing still validates its own current query. */
    void **index=PTR(PTR(ui,0x1c),0xaac);
    *number=HALF(PTR(index[g->first[group]],0x20),0x88);
    return (i32)(g->first[group]+group+1);
}

u32 discs_fast_number(void *list,u32 row)
{
    void *ui;
    struct groups *g=list_groups(list,&ui);
    if (!g || g->count<2 || row>=WORD(list,0xa0)) return 0;
    u32 header,song=song_row(g,row,&header);
    if (header) return 0;
    void **index=PTR(PTR(ui,0x1c),0xaac);
    return HALF(PTR(index[song],0x20),0x88);
}
