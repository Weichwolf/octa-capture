/* SPDX-License-Identifier: GPL-2.0-only */
/* Host-test types only; never used by Kbuild. */
#ifndef OCTA_CLOCK_TEST_TYPES_H
#define OCTA_CLOCK_TEST_TYPES_H
#ifdef __cplusplus
#include <cstdint>
using u8 = std::uint8_t;
using u16 = std::uint16_t;
#else
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
#endif
#endif
