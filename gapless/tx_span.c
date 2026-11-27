/* 4G 4.3.1.1 primary PCM output: locate the I2S TX endpoint of a stereo16
 * output queue, and check that the queue's sample-rate conversion is the
 * identity (handoff.S may then keep the ring across a codec change). */
#include <stdint.h>

static uint32_t word(const void *p, unsigned offset) {
  return *(volatile const uint32_t *)((uintptr_t)p + offset);
}
static uint8_t byte_at(const void *p, unsigned offset) {
  return *(volatile const uint8_t *)((uintptr_t)p + offset);
}
static void *pointer_at(const void *p, unsigned offset) {
  return (void *)(uintptr_t)word(p, offset);
}

/* The two stock stereo output adapters keep their I2S object at different
 * offsets. That object's TX endpoint owns the physical queue and callbacks. */
uint32_t gapless_output_tx(const void *queue) {
  if (!queue || byte_at(queue, 0x69) || byte_at(queue, 0x94) != 2 ||
      byte_at(queue, 0x95) != 16 || !word(queue, 0xd0) || word(queue, 0xd4))
    return 0;
  void *driver = pointer_at(queue, 0x70);
  if (!driver)
    return 0;
  uint32_t table = word(driver, 0);
  if (table != 0x21eb4 && table != 0x21f3c)
    return 0;
  void *i2s = pointer_at(driver, table == 0x21eb4 ? 0x1c : 0x38);
  if (!i2s || word(i2s, 0) != 0x236ec || !word(i2s, 0xc))
    return 0;
  return word(i2s, 0x18);
}

int gapless_mixed_dsp_eligible(const void *queue) {
  if (!gapless_output_tx(queue) || word(queue, 0xec))
    return 0;
  const void *engine = pointer_at(queue, 0x5c);
  if (!engine)
    return 0;
  const void *converter = pointer_at(engine, 0x90bc);
  if (converter != pointer_at(queue, 0x48))
    return 0;
  if (!converter)
    return word(engine, 0x2c) != 1; /* 0x115b84 bypasses conversion entirely. */
  /* 0x12a35c..388 selects mode 2 for identical input and output rates.
   * Other modes and the time stretcher size work from mutable codec state. */
  return word(converter, 0) == 0x28eb8 && byte_at(converter, 0x80) == 2;
}
