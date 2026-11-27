/* r19's letter-mode redraw boundary only. Ordinary wheel selection never
 * enters here. Native top-reveal retains the full last-page clamp. */
#include "interval.h"
#define WORD(p, n) (*(u32 *)((u8 *)(p) + (n)))
#define PTR(p, n) (*(void **)((u8 *)(p) + (n)))

void pomo_fast_scroll_top(void *list) {
    void *row = PTR(list, 0x90);
    if (PTR(list, 0x10) && row && (i32)(WORD(row, 0x60) - WORD(row, 0x58)) > 0) {
        u32 selected = FN(0x96fc4, u32 (*)(void *))(list);
        FN(0xab52c, void (*)(void *, u32))(list, selected);
    }
    FN(0x9107c, void (*)(void *))(list);
}
