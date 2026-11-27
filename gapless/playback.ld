/* The old OSOS end marker moves after the new ARM instructions. */
SECTIONS
{
    .text 0x00356f10 : {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    }
    /* Same RAM alias used by the stock PCM engine and I2S globals.
       The image builder extends the stock startup zero-fill and heap bound. */
    .bss 0x1452f014 (NOLOAD) : {
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
    }
    .data : { *(.data .data.*) }
    /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .note* .eh_frame*) }
}
gapless_fw_base = 0;
ASSERT(SIZEOF(.data) == 0, "initialized RAM requires a startup copy")
ASSERT(ADDR(.text) + SIZEOF(.text) + 4 <= 0x390000, "OSOS collides with initialized RAM")
