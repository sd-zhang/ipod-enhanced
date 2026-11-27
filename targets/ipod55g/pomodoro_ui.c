/* Native 5.5G iPod Renderer integration. No RTC-thread UI calls. */
#include "pomodoro_runtime.h"
#define WORD(p,n) (*(u32 *)((u8 *)(p)+(n)))
#define PTR(p,n) (*(void **)((u8 *)(p)+(n)))
#define STR 0x53747220u
#define BMAP 0x424d6170u
#define PHASE 0x7122u
#define COUNTDOWN 0x7123u
#define ACTION 0x7124u
#define RESET_LABEL 0x7125u
#define BUTTON_GROUP 0x7121u
#define LEFT_BUTTON 0x7127u
#define RIGHT_BUTTON 0x7128u

u32 pomo_ui_timer[10];
u32 pomo_ui_ready;
volatile u32 pomo_ui_visible;
u32 pomo_ui_phase;
u32 pomo_ui_selected;
/* Native Stopwatch widgets copy this UI-thread-owned UTF-8 string. */
static char countdown[6];

static int active(void *owner) {
    void *menu=owner?PTR(owner,0x28):(void *)0;
    return menu && (WORD(menu,0x1c)&0xffffu)==BUTTON_GROUP;
}
static void stop(void) { FN(0x151c98,void (*)(void *))(pomo_ui_timer); }
static void detach_buttons(void *owner) {
    if(!owner)return;
    void *group=active(owner)?PTR(owner,0x28):(void *)0;
    /* Look up the owned live widget if native focus has already moved away.
     * Never retain a widget pointer across screen changes or destruction. */
    if(!group && PTR(owner,0x18))
        group=FN(0x19d3b0,void *(*)(void *,u32))(owner,BUTTON_GROUP);
    if(group && FN(0x149ec4,int (*)(void *,void *))(group,owner))
        FN(0x14a178,void (*)(void *,void *))(group,owner);
}
static void refresh_buttons(void *owner) {
    FN(0x21f37c,void (*)(void *,u32,u32))(owner,BMAP,LEFT_BUTTON);
    FN(0x21f37c,void (*)(void *,u32,u32))(owner,BMAP,RIGHT_BUTTON);
}

int pomo_ui_property(void *owner,u32 tag,u32 key,void **out) {
    /* Stock screens never need the owned timer/bitmap dispatch below. Keep
     * their frequent property requests on a short native-provider path. */
    if(key-PHASE>RIGHT_BUTTON-PHASE)
        return FN(0x1d021c,int (*)(void *,u32,u32,void **))(owner,tag,key,out);
    if(tag==BMAP && (key==LEFT_BUTTON || key==RIGHT_BUTTON)) {
        u32 selected=(key==LEFT_BUTTON?0u:1u)==pomo_ui_selected;
        *out=FN(0x21f3dc,void *(*)(u32,u32))(BMAP,selected?0x56be:0x56bb);
        return *out!=0;
    }
    if(tag==STR && key==COUNTDOWN) {
        State state;pomo_snapshot(&state,1);
        u32 remaining=state.remaining>1500?1500:state.remaining;
        u32 minutes=remaining/60,seconds=remaining%60;
        countdown[0]='0'+minutes/10;countdown[1]='0'+minutes%10;
        countdown[2]=':';
        countdown[3]='0'+seconds/10;countdown[4]='0'+seconds%10;
        countdown[5]=0;
        *out=countdown;return 1;
    }
    if(tag==STR && (key==PHASE || key==ACTION || key==RESET_LABEL)) {
        if(key==RESET_LABEL) {*out="Reset";return 1;}
        State state;pomo_snapshot(&state,0);
        *out=key==PHASE?(state.phase?"Break":"Focus"):(state.running?"Pause":"Start");
        return 1;
    }
    return FN(0x1d021c,int (*)(void *,u32,u32,void **))(owner,tag,key,out);
}

static void refresh(void *owner,u32 changed) {
    if(!pomo_ui_visible || !active(owner))return;
    State state;pomo_snapshot(&state,0);
    if(changed || state.phase!=pomo_ui_phase) {
        pomo_ui_phase=state.phase;
        FN(0x21f37c,void (*)(void *,u32,u32))(owner,STR,PHASE);
    }
    FN(0x21f37c,void (*)(void *,u32,u32))(owner,STR,COUNTDOWN);
    if(changed) {
        FN(0x21f37c,void (*)(void *,u32,u32))(owner,STR,ACTION);
    }
    if(state.running)FN(0x151694,void (*)(void *))(pomo_ui_timer);
    else stop();
}

void pomo_ui_command(void *owner,i32 command) {
    if(command==-0x5501) {
        pomo_ui_visible=0;detach_buttons(owner);if(pomo_ui_ready)stop();return;
    }
    if(command==-0x5500) {
        if(!active(owner))return;
        void *group=PTR(owner,0x28);
        if(!FN(0x149ec4,int (*)(void *,void *))(group,owner))
            FN(0x149f28,void (*)(void *,void *))(group,owner);
        if(!pomo_ui_ready || pomo_ui_timer[8]!=(u32)owner) {
            if(pomo_ui_ready)stop();
            FN(0x13d7c8,void (*)(void *,void *,u32,u32))(owner,pomo_ui_timer,0,0);
            FN(0x151c24,void (*)(void *,u32))(pomo_ui_timer,1000);
            pomo_ui_ready=1;
        }
        pomo_ui_selected=0;pomo_ui_visible=1;
        refresh_buttons(owner);refresh(owner,1);return;
    }
    if(command==-0x5502 || command==-0x5503 || command==-0x5504) {
        if(pomo_ui_visible && active(owner)) {
            pomo_control(command==-0x5504?pomo_ui_selected:command==-0x5503);
            refresh(owner,1);
        }
        return;
    }
    FN(0x19e790,void (*)(void *,i32))(owner,command);
}

int pomo_ui_event(void *owner,void *event) {
    if(event && WORD(event,4)==16) {
        void *payload=PTR(event,8);
        if(payload && WORD(payload,0)==0x66eb94u && WORD(payload,4)==BUTTON_GROUP) {
            if(pomo_ui_visible && active(owner) && WORD(payload,8)==4 && PTR(payload,12)) {
                i32 delta=*(i32 *)PTR(payload,12);
                u32 selected=pomo_ui_selected;
                if(delta==-1)selected=0;
                else if(delta==1)selected=1;
                if(selected!=pomo_ui_selected) {
                    pomo_ui_selected=selected;refresh_buttons(owner);
                }
            }
            return 1;
        }
    }
    if(event && WORD(event,4)==9 && PTR(event,8)==pomo_ui_timer) {
        if(pomo_ui_visible && active(owner))refresh(owner,0);
        else {pomo_ui_visible=0;detach_buttons(owner);if(pomo_ui_ready)stop();}
        return 1;
    }
    return FN(0x19f2e0,int (*)(void *,void *))(owner,event);
}

/* Native1519e8 worker replacement: unrelated timers retain publication and
 * cleanup. A rejected Pomodoro event retries only after in-flight cleanup. */
extern State pomo_state;
#define TIMER_STOP 0x73746f70u
#define TIMER_RUN 0x72756e20u
#define TIMER_EXPIRED 0x65787069u
void pomo_timer_deliver(volatile u32 *timer) {
    if(timer!=pomo_ui_timer) {
        FN(0x151934,void (*)(volatile u32 *))(timer);
        FN(0xd1e24,void (*)(volatile u32 *))(timer);
        return;
    }
    /* Native21f20c copies seven words, including three unused trailing words. */
    u32 event[7];
    FN(0xc9a84,void (*)(void *))((void *)0x1081e10c);
    timer[7]=TIMER_EXPIRED;
    event[0]=0x74696d65u;
    FN(0xccebc,void (*)(u32 *))(&event[1]);
    event[2]=0;event[3]=(u32)timer;
    event[4]=event[5]=event[6]=0;
    u32 accepted=FN(0x1517d4,u32 (*)(void *,void *,u32 *,u32,u32))
        (*(void **)0x1081e0e4,(void *)timer[9],event,0,0);
    FN(0xc9b58,void (*)(void *))((void *)0x1081e10c);
    FN(0xd1e24,void (*)(volatile u32 *))(timer);
    if(accepted)return;
    /* Only aligned runtime state is read here. No RTC lock or UI-object access.
     * Native recursive scheduler lock serializes this check with start/stop. */
    FN(0xc9a84,void (*)(void *))((void *)0x10800014);
    if(pomo_ui_visible && *(volatile u32 *)&pomo_state.running &&
       timer[6]==TIMER_STOP && timer[7]==TIMER_EXPIRED) {
        timer[7]=TIMER_RUN;
        FN(0xc6c50,void (*)(volatile u32 *))(timer);
    }
    FN(0xc9b58,void (*)(void *))((void *)0x10800014);
}
