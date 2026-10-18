// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/Protocol.hpp"

#include <iosfwd>

namespace octa {

// Validate the complete state before emitting any JSON. Does not append a newline.
[[nodiscard]] bool write_state_json(std::ostream &out, const Message &message);

// Emit a complete activity event, including inactive/duplicate notifications.
// Unknown or invalid messages emit nothing. Bulk state does not contain activity.
[[nodiscard]] bool write_auto_sense_activity_json(std::ostream &out, const Message &message);

}
