// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <iosfwd>

namespace octa {

// Validate the complete state before emitting any JSON. Does not append a newline.
[[nodiscard]] bool write_state_json(std::ostream &out, const Message &message);

}
