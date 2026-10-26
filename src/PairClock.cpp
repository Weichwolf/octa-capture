// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairClock.hpp"

#include "octa/DeviceStatus.hpp"
#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <optional>

namespace octa {
namespace {
constexpr std::array<std::uint32_t, 3> rates = {44100, 48000, 96000};
constexpr unsigned int poll_limit = 40;
constexpr auto poll_delay = std::chrono::milliseconds(200);
constexpr auto poll_timeout = std::chrono::seconds(8);
using Clocks = std::array<DeviceStatus, 2>;

bool supported(std::uint32_t rate) {
  return std::ranges::find(rates, rate) != rates.end();
}

bool consistent(const DeviceStatus &status) {
  const auto decoded = decode_device_status(status.raw);
  return decoded && *decoded == status;
}

std::expected<Clocks, StatusReadError> read_clocks(PairClockIo &io) {
  const auto base = io.read(PairUnit::base);
  if (!base) { return std::unexpected(base.error()); }
  const auto expansion = io.read(PairUnit::expansion);
  if (!expansion) { return std::unexpected(expansion.error()); }
  if (!consistent(*base) || !consistent(*expansion)) {
    return std::unexpected(
        StatusReadError{.code = -EPROTO, .operation = "paired clock status fields"});
  }
  return Clocks{*base, *expansion};
}

bool master(const DeviceStatus &status) {
  // Both references restrict manual rates when the entire setting byte is set.
  return status.clock_source == ClockSource::internal && status.raw[6] == 0;
}

bool ready(const Clocks &clocks, std::uint32_t rate) {
  return clocks[0].clock_source == ClockSource::external && clocks[0].clock_active &&
         master(clocks[1]) && clocks[0].sample_rate_hz == rate && clocks[1].sample_rate_hz == rate;
}

bool unchanged(const Clocks &first, const Clocks &second) {
  for (std::size_t index = 0; index < first.size(); ++index) {
    if (first[index].sample_rate_hz != second[index].sample_rate_hz ||
        first[index].raw[6] != second[index].raw[6] ||
        first[index].clock_active != second[index].clock_active) {
      return false;
    }
  }
  return true;
}

std::expected<void, StatusReadError> wait_for(PairClockIo &io, std::uint32_t rate, bool recovery) {
  const auto deadline = io.now() + poll_timeout;
  std::optional<Clocks> previous;
  for (unsigned int attempt = 0; attempt < poll_limit; ++attempt) {
    if (!recovery && io.cancelled()) {
      return std::unexpected(
          StatusReadError{.code = -EINTR, .operation = "cancel paired clock change"});
    }
    if (io.now() >= deadline) { break; }
    const auto clocks = read_clocks(io);
    if (!recovery && io.cancelled()) {
      return std::unexpected(
          StatusReadError{.code = -EINTR, .operation = "cancel paired clock change"});
    }
    if (io.now() >= deadline) { break; }
    if (!clocks && (clocks.error().code == -ENODEV || clocks.error().code == -EACCES)) {
      return std::unexpected(clocks.error());
    }
    if (clocks && ready(*clocks, rate)) {
      if (previous && unchanged(*previous, *clocks)) { return {}; }
      previous = *clocks;
    } else {
      previous.reset();
    }
    if (attempt + 1 < poll_limit) { io.pause(poll_delay); }
  }
  return std::unexpected(StatusReadError{.code = -ETIMEDOUT, .operation = "paired clock settling"});
}

std::expected<void, StatusReadError> recover(PairClockIo &io, std::uint32_t rate) {
  const auto expansion = io.read(PairUnit::expansion);
  if (!expansion) { return std::unexpected(expansion.error()); }
  if (!consistent(*expansion) || !master(*expansion)) {
    return std::unexpected(
        StatusReadError{.code = -EPERM, .operation = "paired recovery master changed"});
  }
  const auto prepared = io.prepare_expansion(rate);
  if (!prepared) { return prepared; }
  return wait_for(io, rate, true);
}

std::expected<Clocks, StatusReadError> preflight(PairClockIo &io) {
  const auto first = read_clocks(io);
  if (!first) { return first; }
  if (!supported((*first)[0].sample_rate_hz) || !ready(*first, (*first)[0].sample_rate_hz)) {
    return std::unexpected(
        StatusReadError{.code = -EPERM, .operation = "paired clock rates and roles"});
  }
  const auto second = read_clocks(io);
  if (!second) { return second; }
  if (!unchanged(*first, *second)) {
    return std::unexpected(
        StatusReadError{.code = -EAGAIN, .operation = "paired clock preflight changed"});
  }
  return second;
}

}

std::expected<PairClockResult, PairClockFailure> change_pair_clock(PairClockIo &io,
                                                                   std::uint32_t rate) {
  if (!supported(rate)) {
    return std::unexpected(PairClockFailure{
        .cause = {.code = -EINVAL, .operation = "paired sample rate"}, .recovery = std::nullopt});
  }
  const auto original = preflight(io);
  if (!original) {
    return std::unexpected(PairClockFailure{.cause = original.error(), .recovery = std::nullopt});
  }
  if (io.cancelled()) {
    return std::unexpected(
        PairClockFailure{.cause = {.code = -EINTR, .operation = "cancel paired clock change"},
                         .recovery = std::nullopt});
  }
  const auto previous_rate = (*original)[0].sample_rate_hz;
  if (previous_rate == rate) {
    return PairClockResult{.previous_rate = previous_rate, .rate = rate, .changed = false};
  }
  const auto prepared = io.prepare_expansion(rate);
  const auto settled = prepared ? wait_for(io, rate, false) : prepared;
  if (settled) {
    return PairClockResult{.previous_rate = previous_rate, .rate = rate, .changed = true};
  }
  const auto restored = recover(io, previous_rate);
  return std::unexpected(
      PairClockFailure{.cause = settled.error(),
                       .recovery = restored ? std::nullopt : std::optional(restored.error()),
                       .change_attempted = true});
}

}
