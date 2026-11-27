/* 4G album disc jumps share the existing letter accelerator and timer.
 * All addresses belong to pinned r36. No retained pointers or extra state. */
typedef unsigned char u8;
typedef unsigned int u32;
typedef signed int i32;
#define WORD(p,o) (*(u32 *)((u8 *)(p)+(o)))
#define PTR(p,o) (*(void **)((u8 *)(p)+(o)))
#define FN(a,t) ((t)(a))
typedef struct {
    u32 native[11], magic, timer[10];
    u32 ready, active, number, last_input, last_step;
    i32 direction;
    u32 stroke_start, stroke_last;
    i32 stroke_distance;
    u32 previous_native_tick;
} LetterState;
extern i32 discs_fast_target(void *,u32,i32,u32 *);
extern u32 discs_fast_number(void *,u32);
extern i32 songs_fast_target(void *,u32,i32,u32 *);
extern void discs_jump_redraw(void *,u32);

static LetterState *state(void *list)
{
    LetterState *s=PTR(list,0xe4);
    return s && s->magic==0x4c545234u ? s : (LetterState *)0;
}

static void close_mode(void *list,LetterState *s)
{
    /* Existing key/hide/destructor/event paths own this same state. */
    FN(0x35c084,void (*)(void *,LetterState *,u32))(list,s,1);
}

static i32 current_song(void *list)
{
    if (WORD(list,0)!=0x29030 || (WORD(list,0x1c)&0xffffu)!=0x3301) return -1;
    void *row=PTR(list,0x90);
    if (!row) return -1;
    u32 table=WORD(row,0);
    if (!FN(table+WORD((void *)table,0x120),u32 (*)(void *))(row)) return -1;
    return FN(0x96fc4,i32 (*)(void *))(list);
}

static int heading_is_aligned(void *list,i32 row)
{
    /* Re-evaluate live geometry; preserve native paging and pending direction.
     * Only skip a reveal whose complete rectangle already fits its parent. */
    if (WORD(list,0xa4) || (WORD(list,0xb0)&1)) return 0;
    void *parent=PTR(list,0x10),*item=PTR(list,0x90);
    if (!parent || WORD(parent,0)!=0x22770 || !(WORD(parent,0x20)&2) || !item || row<=0) return 0;
    i32 top=(i32)WORD(parent,0x58);
    i32 viewport=(i32)WORD(parent,0x60)-top;
    i32 height=(i32)WORD(item,0x60)-(i32)WORD(item,0x58);
    /* Nonpositive viewports cannot fit a positive row. Arithmetic halving
     * therefore needs no signed-division rounding correction. */
    if (height<=0 || viewport>128 || height>(viewport>>1)) return 0;
    return (i32)WORD(item,0x58)+(row-1)*height==top &&
        (i32)WORD(item,0x5c)>=(i32)WORD(parent,0x5c) &&
        (i32)WORD(item,0x64)<=(i32)WORD(parent,0x64);
}

u32 discs_fast_wheel(void *list,i32 delta)
{
    i32 current=current_song(list);
    i32 direction=delta<0 ? -1 : 1;
    u32 number;
    i32 target=discs_fast_target(list,(u32)current,direction,&number);
    u32 songs=target<0 && current>=0 &&
        (target=songs_fast_target(list,(u32)current,0,&number))>=0;
    if (target<0)
        return FN(0x37fee0,u32 (*)(void *,i32))(list,delta);
    LetterState *s=state(list);
    if (!s) return 0;
    u32 time=FN(0x87cec,u32 (*)(void))();
    i32 distance=0;
    if (s->previous_native_tick) {
        distance=(i32)s->native[4];
        if ((s->native[8]&0xffffu)==1)
            for (u32 factor=s->native[3];factor>1;factor>>=1) distance>>=1;
    } else s->stroke_distance=0;
    s->previous_native_tick=s->native[0];
    if (s->active && time-s->last_input>=1000000u) close_mode(list,s);
    if (!s->active) {
        /* No forward destination in the final disc: keep native song
         * scrolling and do not arm a popup/timer for an impossible jump. */
        if (!songs && delta>0 && target<=current) {
            s->stroke_distance=0;
            return 0;
        }
        /* Remember the stroke's starting letter before ordinary movement.
         * The first native sample can have no previous acceleration tick. */
        if (songs && !distance) distance=delta;
        if (distance) {
            if (!s->stroke_distance || time-s->stroke_start>=500000u ||
                time-s->stroke_last>=200000u ||
                (distance<0)!=(s->stroke_distance<0)) {
                s->stroke_start=time;
                s->stroke_distance=0;
                if (songs) s->number=number;
            }
            s->stroke_last=time;
            s->stroke_distance+=distance;
        }
        if (s->stroke_distance<64 && s->stroke_distance>-64 &&
            delta<16 && delta>-16) return 0;
        if (songs && s->number==number) return 0;
    }
    if (!delta) return s->active;
    u32 entering=!s->active;
    s->last_input=time;
    if (!s->ready) {
        FN(0x8bddc,void (*)(void *,void *,u32))(list,s->timer,0);
        s->ready=1;
    }
    if (s->timer[1]!=1000)
        FN(0xcdd84,void (*)(void *,u32))(s->timer,1000);
    FN(0xd2bd0,void (*)(void *))(s->timer);
    if (!entering && direction==s->direction && time-s->last_step<150000u) return 1;
    if (songs) {
        if (entering) {
            /* Stock 5G entry keeps the ordinary landing, then later wheel
             * input jumps groups. Respect the native Songs bottom limit. */
            target=current+delta;
            if (target<0) target=0;
            if ((u32)target>=WORD(list,0xa0)) target=(i32)WORD(list,0xa0)-1;
            songs_fast_target(list,(u32)target,0,&number);
        } else target=songs_fast_target(list,(u32)current,direction,&number);
    }
    s->active=1;
    s->direction=direction;
    s->last_step=time;
    u32 previous=s->number;
    s->number=number;
    /* At the forward boundary the lookup returns this disc's first song.
     * Do not turn that clamped target into a backward selection or reveal. */
    u32 moving=target>current || (target<current && direction<0);
    if (moving) {
        WORD(list,0xa4)=(u32)direction;
        FN(0xd10e8,void (*)(void *,u32))(list,(u32)target);
        FN(0x625f4,void (*)(u32))(1);
    } else if (!songs && target==current && !heading_is_aligned(list,current)) {
        /* Entry already on the final disc's first song still needs reveal. */
        FN(0xbfdd4,void (*)(void *))(list);
    }
    if (entering || moving || previous!=s->number) {
        /* Reuse named-list letter mode's guarded top reveal and invalidate.
         * Native viewport code retains the last-page clamp. Disc mode keeps
         * its separate heading-aware reveal above. */
        discs_jump_redraw(list,songs);
    }
    return 1;
}

static const u8 digits[10][7]={
    {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},
    {14,17,1,2,4,8,31},{30,1,1,14,1,1,30},
    {2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
    {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},
    {14,17,17,14,17,17,14},{14,17,17,15,1,1,14}
};

static void fill(void *context,i32 top,i32 left,i32 bottom,i32 right)
{
    i32 box[4]={top,left,bottom,right};
    FN(0xa4a38,void (*)(void *,const i32 *))(context,box);
}

void discs_fast_draw(void *list,void *model,const i32 *clip)
{
    LetterState *s=state(list);
    /* Ordinary repaints need no disc navigation. An unfinished stroke still
     * takes the checked path below so query invalidation cancels it. */
    if (!s || (!s->active && !s->stroke_distance)) {
        FN(0x35c304,void (*)(void *,void *,const i32 *))(list,model,clip);
        return;
    }
    i32 current=current_song(list);
    u32 number=discs_fast_number(list,(u32)current);
    if (!number) {
        if ((WORD(list,0x1c)&0xffffu)==0x3301) {
            if (current<0 || songs_fast_target(list,(u32)current,0,&number)<0)
                close_mode(list,s);
            else if (s->active && s->number!=number) {
                /* A rebuilt live index can keep the same row/count but
                 * change its letter. Preserve the inactive stroke origin. */
                s->number=number;
                FN(0x9107c,void (*)(void *))(list);
            }
        }
        /* Non-disc lists retain the entire existing popup/recovery path. */
        FN(0x35c304,void (*)(void *,void *,const i32 *))(list,model,clip);
        return;
    }
    if (s->active &&
        FN(0x87cec,u32 (*)(void))()-s->last_input>=1000000u) close_mode(list,s);
    FN(0x5cdc4,void (*)(void *,void *,const i32 *))(list,model,clip);
    if (!s->active || !FN(0xe3ddc,u32 (*)(void *))(list)) return;
    const i32 *visible=(const i32 *)((u8 *)list+0x78);
    i32 height=visible[2]-visible[0],width=visible[3]-visible[1];
    if (height<44 || width<44) return;
    /* Decimal conversion uses subtraction, avoiding runtime division helpers. */
    const u32 powers[5]={10000,1000,100,10,1};
    u8 text[5]; u32 count=0;
    for (u32 i=0;i<5;++i) {
        u32 digit=0;
        while (number>=powers[i]) { number-=powers[i]; ++digit; }
        if (digit || count || i==4) text[count++]=(u8)digit;
    }
    u32 scale=4;
    while ((6*count-1)*scale>36) --scale;
    u32 context[17];
    i32 bounded[4];
    for (u32 i=0;i<4;++i)
        bounded[i]=i<2 ? (clip[i]>visible[i] ? clip[i] : visible[i]) :
                        (clip[i]<visible[i] ? clip[i] : visible[i]);
    if (bounded[0]>=bounded[2] || bounded[1]>=bounded[3]) return;
    FN(0xf745c,void *(*)(void *))(context);
    FN(0x66efc,void (*)(void *,void *))(list,context);
    FN(0xca9d8,void (*)(void *,void *,const i32 *))
        (context,*(void **)0x103cdcc8,bounded);
    i32 top=visible[0]-(i32)WORD(list,0x58)+(height-44)/2;
    i32 left=visible[1]-(i32)WORD(list,0x5c)+(width-44)/2;
    u32 black=0,white=0xffffff;
    FN(0xcac3c,void (*)(void *,const void *))(context,&black);
    fill(context,top,left,top+44,left+44);
    FN(0xcac3c,void (*)(void *,const void *))(context,&white);
    fill(context,top+2,left+2,top+42,left+42);
    FN(0xcac3c,void (*)(void *,const void *))(context,&black);
    i32 y0=top+(44-7*(i32)scale)/2;
    i32 x0=left+(44-(6*(i32)count-1)*(i32)scale)/2;
    for (u32 n=0;n<count;++n)
        for (u32 y=0;y<7;++y)
            for (u32 x=0;x<5;++x) {
                if (!(digits[text[n]][y]&(16u>>x))) continue;
                u32 start=x;
                while (x+1<5 && (digits[text[n]][y]&(16u>>(x+1)))) ++x;
                fill(context,y0+y*scale,x0+(6*n+start)*scale,
                     y0+(y+1)*scale,x0+(6*n+x+1)*scale);
            }
    FN(0x102318,void (*)(void *,u32))(context,0);
}
