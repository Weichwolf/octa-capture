// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <string_view>

namespace octa::cli {

struct ApplyOptions {
  std::string_view port;
  const char *snapshot_path;
};

int apply_preset(const Preset &preset, ApplyOptions options);
int restore_snapshot(const Message &snapshot, std::string_view port);

}
