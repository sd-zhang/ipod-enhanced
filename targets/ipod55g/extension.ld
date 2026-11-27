ENTRY(next_value)
SECTIONS
{
    . = 0x7358c0;
    .text : { *(.text*) }
    .rodata : { *(.rodata*) }
    . = ALIGN(16);
    . += 0x10000000;
    .data : AT(ADDR(.data) - 0x10000000) { *(.data*) }
    .bss (NOLOAD) : { *(.bss*) *(COMMON) }
    __extension_end = ALIGN(4) - 0x10000000;
    /DISCARD/ : { *(.comment) *(.ARM.attributes) *(.ARM.exidx*) *(.ARM.extab*) }
}
