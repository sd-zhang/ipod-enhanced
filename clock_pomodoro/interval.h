#ifndef POMODORO_INTERVAL_H
#define POMODORO_INTERVAL_H
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef int i32;
#define FN(a, t) ((t)(a))

typedef struct {
    u16 year;
    u8 month, day;
    u16 year_day;
    u8 weekday, zone, hour, minute, second, flags;
} Date;

typedef struct {
    Date deadline;
    u32 phase;      /* 0 Focus, 1 Short Break, 2 Long Break */
    u32 round;      /* 0..3; the focus round associated with this phase */
    u32 running;
    u32 remaining;
    u32 transitions;
} State;

typedef struct {
    Date date;
    u16 reserved;
    u8 enabled, wake;
    u32 event;
} Alarm;

void pomo_control(u32 action);
void pomo_snapshot(State *out, u32 with_remaining);
#endif
