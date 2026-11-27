/* Stock 5.5G 1.3 UI-thread conveniences. No retained metadata/queue pointers. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define W(p,o) (*(u32 *)((u8 *)(p)+(o)))
#define H(p,o) (*(u16 *)((u8 *)(p)+(o)))
#define F(a,t) ((t)(a))

static int safe_parents(void) {
    u8 *app=F(0x1d30d8,u8 *(*)(void))();
    if (!app) return 0;
    u8 *history=(u8 *)W(app,0xe0);
    if (!history || W(history,0)!=0x6670b4 || history[8]) return 0;
    u8 *node=(u8 *)W(history,4);
    const u32 scripts[]={5094,4244,4002};
    /* Reuse only the complete, ordinary Actions -> NP -> Home chain.
     * Old library parents still require the stock unwind/cleanup path. */
    for (u32 i=0;i<3;++i) {
        if (!node || W(node,0)!=0x66dbf0 || node[0x1c] || node[0x1d] ||
            W(node,0x24)!=scripts[i]*4+2) return 0;
        node=(u8 *)W(node,0x10);
    }
    return !node;
}

static int browse(u8 *ui, int operation, int album) {
    if (!ui || !W(ui,0x1c) || W(ui,0x28)!=0x676608 ||
        !F(0x255c60,int (*)(u8 *))(ui+0x28)) return 0;
    u16 name[256];
    u8 *track=(u8 *)W(ui,0x2c), *query=(u8 *)W(ui,0x1c);
    if (F(album?0x9f8a4:0x9f8f0,int (*)(u8 *,u16 *))(track,name) ||
        !name[0] || name[0]>255) return 0;
    if (operation==-2) return 1;
    /* Native source selection clears all browse filters/indexes. Playback has
     * its own SPlaylist (ui+0x20), never replaced by this browse operation. */
    if (F(0xb29b0,int (*)(u8 *,int))(query,0)) return 0;
    F(0xb2894,void (*)(u8 *,int,int))(query,1,3);
    F(0x1e7628,void (*)(u8 *,int))(ui,3);
    u16 *filter=(u16 *)(query+(album?0x628:0x228));
    for (u32 i=0;i<=name[0];++i) filter[i]=name[i];
    for (u32 o=0xc0;o<=0xd0;o+=4) W(ui,o)=~0u;
    for (u32 o=0x3ec;o<=0x404;o+=4) W(ui,o)=~0u;
    ui[0x48d]=album?9:5;
    return 1;
}

struct context { u8 *query,*base,*playing,*queue,*otg,*item; int removing; };
static int current(u8 *ui, struct context *c) {
    if (!ui || !(c->query=(u8 *)W(ui,0x1c)) ||
        !(c->playing=(u8 *)W(ui,0x20))) return 0;
    c->base=F(0x9bc6c,u8 *(*)(u8 *))(c->query);
    if (!c->base || !(c->queue=(u8 *)W(c->playing,4)) ||
        !(c->otg=(u8 *)W(c->base,0xce0)) ||
        !F(0xad56c,int (*)(u8 *))(c->queue) ||
        !F(0xad56c,int (*)(u8 *))(c->otg) ||
        W(c->queue,0xc)!=W(c->otg,0xc) || !W(c->otg,0x28) ||
        (int)W(c->playing,0x14)<0) return 0;
    /* The exact playing list entry is essential: duplicate songs are distinct.
     * Match the native SPlaylist accessor's shuffle and podcast order flags. */
    c->item=F(0x9c838,u8 *(*)(u8 *,int,int,u32))(c->queue,
        (c->queue[0xec]&1)?2:1,(c->queue[0xed]>>1)&1,W(c->playing,0x14));
    if (!c->item || W(c->item,0)!=(u32)c->queue ||
        !W(c->item,0x10) || !W(c->item,0x20) || (c->item[0x1d]&1)) return 0;
    c->removing=c->queue==c->otg;
    return 1;
}

static int otg(u8 *ui,int operation) {
    u32 scope[2];
    F(0x17ca6c,void (*)(u32 *))(scope);
    struct context c;
    int valid=current(ui,&c), success=0;
    if (operation==-5) {
        int view=valid?(c.removing?0x7107:0x7100):0x710a;
        F(0x17ca8c,void (*)(u32 *))(scope);
        return view;
    }
    if (!valid || c.removing!=(operation==-7) || !W(c.base,0xca0)) goto end;
    if (operation==-6) {
        if (H((u8 *)W(c.otg,0x28),0x32)>=1000) goto end;
        if (!F(0x93504,u8 *(*)(u8 *,u8 *,int,int))(c.item,c.otg,0,0)) goto end;
    } else {
        /* dpli notification runs before unlink/free. Do not touch item after. */
        if (F(0x95958,int (*)(u8 *))(c.item)) goto end;
        F(0xad504,void (*)(u8 *))(c.otg);
        F(0xa376c,void (*)(u8 *))(c.query);
        F(0xa37a8,void (*)(u8 *))(c.query);
    }
    ((u8 *)W(c.base,0xca0))[0xb76]=1;
    success=1;
end:
    F(0x17ca8c,void (*)(u32 *))(scope);
    if (success) {
        if (operation==-6) F(0x1ea6f8,void (*)(u8 *))(ui);
        else F(0x1e7f08,void (*)(u8 *))(ui);
    }
    return success;
}

int actions_artist(u8 *ui,int operation) {
    if (operation==-2 || operation==-3) return browse(ui,operation,0);
    return F(0x1e969c,int (*)(u8 *,int))(ui,operation);
}
int actions_album(u8 *ui,int operation) {
    if (operation==-2 || operation==-3) return browse(ui,operation,1);
    if (operation==-4) return safe_parents();
    if (operation>=-7 && operation<=-5) return otg(ui,operation);
    return F(0x1e8e04,int (*)(u8 *,int))(ui,operation);
}
