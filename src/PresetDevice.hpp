// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/AutoSense.hpp"
#include "octa/Midi.hpp"
#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <chrono>
#include <cstdint>
#include <string_view>

namespace octa::cli {

struct ApplyOptions {
  std::string_view port;
  const char *snapshot_path;
};

struct AutoSenseOptions {
  std::uint8_t channels;
  std::chrono::milliseconds duration;
  AutoSenseAction action;
};

enum class RoutingPreset : std::uint8_t { multi_monitor, playback };

int apply_direct_out(ApplyOptions options);
int apply_routing(RoutingPreset preset, ApplyOptions options);
int run_auto_sense(AutoSenseOptions request, ApplyOptions options);
int operate_memory(MemoryAction action, ApplyOptions options);
int apply_preset(const Preset &preset, ApplyOptions options);
int restore_snapshot(const Message &snapshot, std::string_view port, bool cancel_auto = false);

}
