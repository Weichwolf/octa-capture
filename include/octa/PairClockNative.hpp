// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/PairClock.hpp"
#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <array>
#include <csignal>
#include <cstdint>
#include <expected>
#include <filesystem>
#include <string_view>

namespace octa {

struct PairIdentity {
  std::string_view card;
  std::string_view serial;
};

// Requires retained native PCM/control handles with disconnect checks; an open
// PCM alone does not prevent reuse of its card number after disconnection.
// The alternate sound root is for fixed filesystem fixtures, not CLI input.
[[nodiscard]] std::expected<void, StatusReadError>
verify_pair_usb_identity(int card,
                         std::string_view serial,
                         PairUnit unit,
                         const std::filesystem::path &sound_root = "/sys/class/sound");

// Experimental: requires both audio servers/clients to have released both units.
// Prepares only EXP capture and never starts or transfers either PCM stream.
[[nodiscard]] std::expected<PairClockResult, PairClockFailure>
change_native_pair_clock(const std::array<PairIdentity, 2> &identities,
                         std::uint32_t rate,
                         const volatile std::sig_atomic_t &cancelled,
                         const std::filesystem::path &sound_root = "/sys/class/sound");

}
