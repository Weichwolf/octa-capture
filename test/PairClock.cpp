// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairClock.hpp"

#include "octa/DeviceStatus.hpp"
#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <functional>
#include <iostream>
#include <optional>
#include <string_view>
#include <vector>

namespace {
constexpr std::array<std::uint32_t, 3> rates = {44100, 48000, 96000};
constexpr unsigned int polls = 40;
constexpr auto delay = std::chrono::milliseconds(200);
using Reply = std::expected<void, octa::StatusReadError>;

octa::DeviceStatus status(std::uint32_t rate, bool external) {
  constexpr unsigned int middle_shift = 8;
  constexpr unsigned int high_shift = 16;
  constexpr std::uint32_t mask = 255;
  const std::array<std::uint8_t, 8> raw = {1,
                                           0,
                                           0,
                                           static_cast<std::uint8_t>(rate & mask),
                                           static_cast<std::uint8_t>((rate >> middle_shift) & mask),
                                           static_cast<std::uint8_t>((rate >> high_shift) & mask),
                                           static_cast<std::uint8_t>(external),
                                           1};
  return *octa::decode_device_status(raw);
}

struct Peer final : octa::PairClockIo {
  std::array<octa::DeviceStatus, 2> clocks = {status(rates[1], true), status(rates[1], false)};
  unsigned int reads = 0;
  std::vector<octa::PairUnit> units;
  std::vector<std::uint32_t> writes;
  std::chrono::steady_clock::time_point time;
  std::chrono::milliseconds read_time{};
  unsigned int pauses = 0;
  bool cancelled_flag = false;
  std::optional<octa::StatusReadError> read_error;
  std::function<void(Peer &, octa::PairUnit)> on_read;
  std::function<Reply(Peer &, std::uint32_t)> on_prepare;
  std::function<void(Peer &)> on_pause;

  void set_rate(std::uint32_t rate) { clocks = {status(rate, true), status(rate, false)}; }

  std::expected<octa::DeviceStatus, octa::StatusReadError> read(octa::PairUnit unit) override {
    ++reads;
    units.push_back(unit);
    time += read_time;
    if (on_read) { on_read(*this, unit); }
    if (read_error) { return std::unexpected(*read_error); }
    return clocks[unit == octa::PairUnit::base ? 0 : 1];
  }

  Reply prepare_expansion(std::uint32_t rate) override {
    writes.push_back(rate);
    if (on_prepare) { return on_prepare(*this, rate); }
    set_rate(rate);
    return {};
  }

  [[nodiscard]] std::chrono::steady_clock::time_point now() const noexcept override { return time; }

  void pause(std::chrono::milliseconds duration) override {
    ++pauses;
    time += duration;
    if (duration != delay) { read_error = {.code = -EINVAL, .operation = "test poll interval"}; }
    if (on_pause) { on_pause(*this); }
  }

  [[nodiscard]] bool cancelled() const noexcept override { return cancelled_flag; }
};

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL " << message << '\n'; }
  return condition;
}

bool successful_rate(std::uint32_t old_rate, std::uint32_t new_rate) {
  Peer peer;
  peer.set_rate(old_rate);
  const auto result = octa::change_pair_clock(peer, new_rate);
  const bool changed = old_rate != new_rate;
  const std::vector<std::uint32_t> expected =
      changed ? std::vector{new_rate} : std::vector<std::uint32_t>{};
  if (!require(result && result->previous_rate == old_rate && result->rate == new_rate &&
                   result->changed == changed && peer.writes == expected &&
                   peer.reads == (changed ? 8U : 4U) && peer.pauses == (changed ? 1U : 0U),
               "every supported rate pair requires two coherent snapshots and only EXP prepares")) {
    return false;
  }
  for (std::size_t index = 0; index < peer.units.size(); ++index) {
    if (!require(peer.units[index] ==
                     (index % 2 == 0 ? octa::PairUnit::base : octa::PairUnit::expansion),
                 "paired snapshots read base then EXP")) {
      return false;
    }
  }
  return true;
}

bool successful_rates() {
  for (const auto old_rate : rates) {
    for (const auto new_rate : rates) {
      if (!successful_rate(old_rate, new_rate)) { return false; }
    }
  }
  return true;
}

bool recovery_is(const std::expected<octa::PairClockResult, octa::PairClockFailure> &result,
                 int code) {
  if (result) { return false; }
  const auto &recovery = result.error().recovery;
  return recovery && recovery->code == code;
}

bool invalid_requests() {
  constexpr std::array<std::uint32_t, 5> invalid = {0, 1, 47999, 192000, 0xffffffffU};
  for (const auto rate : invalid) {
    Peer peer;
    const auto result = octa::change_pair_clock(peer, rate);
    if (!require(!result && result.error().cause.code == -EINVAL &&
                     !result.error().change_attempted && !result.error().recovery &&
                     peer.reads == 0 && peer.writes.empty(),
                 "unsupported requests fail without I/O or recovery")) {
      return false;
    }
  }
  Peer peer;
  peer.cancelled_flag = true;
  const auto result = octa::change_pair_clock(peer, rates[0]);
  return require(!result && result.error().cause.code == -EINTR &&
                     !result.error().change_attempted && !result.error().recovery &&
                     peer.writes.empty(),
                 "cancellation before mutation does not prepare either unit");
}

bool preflight_errors() {
  for (unsigned int failed = 1; failed <= 4; ++failed) {
    for (const auto error : {-ENODEV, -EACCES, -EIO, -EPROTO}) {
      Peer peer;
      peer.on_read = [failed, error](Peer &io, octa::PairUnit) {
        if (io.reads == failed) {
          io.read_error = {.code = error, .operation = "test preflight read"};
        }
      };
      const auto result = octa::change_pair_clock(peer, rates[0]);
      if (!require(!result && result.error().cause.code == error &&
                       !result.error().change_attempted && !result.error().recovery &&
                       peer.reads == failed && peer.writes.empty(),
                   "every preflight read failure aborts before mutation")) {
        return false;
      }
    }
  }
  return true;
}

enum class Fault : std::uint8_t {
  internal,
  inactive,
  rate,
  source_bits,
  typed_rate,
  phase,
  zero_rate
};

void fault(octa::DeviceStatus &clock, Fault mode) {
  switch (mode) {
    case Fault::internal: clock = status(clock.sample_rate_hz, false); break;
    case Fault::inactive:
      clock.raw[7] = 0;
      clock.clock_active = false;
      break;
    case Fault::rate:
      clock = status(rates[0], clock.clock_source == octa::ClockSource::external);
      break;
    case Fault::source_bits: clock.raw[6] |= 2; break;
    case Fault::typed_rate:
      clock.sample_rate_hz = clock.sample_rate_hz == rates[0] ? rates[1] : rates[0];
      break;
    case Fault::phase: clock.raw[0] = 0; break;
    case Fault::zero_rate:
      clock.raw[3] = 0;
      clock.raw[4] = 0;
      clock.raw[5] = 0;
      break;
    default: break;
  }
}

bool preflight_roles() {
  struct Case {
    std::size_t unit;
    Fault mode;
    int error = -EPERM;
  };

  constexpr std::array cases = {Case{.unit = 0, .mode = Fault::internal},
                                Case{.unit = 0, .mode = Fault::inactive},
                                Case{.unit = 0, .mode = Fault::rate},
                                Case{.unit = 1, .mode = Fault::source_bits},
                                Case{.unit = 0, .mode = Fault::typed_rate, .error = -EPROTO},
                                Case{.unit = 1, .mode = Fault::typed_rate, .error = -EPROTO},
                                Case{.unit = 0, .mode = Fault::phase, .error = -EPROTO},
                                Case{.unit = 1, .mode = Fault::zero_rate, .error = -EPROTO}};
  for (const auto &item : cases) {
    Peer peer;
    fault(peer.clocks[item.unit], item.mode);
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(!result && result.error().cause.code == item.error && peer.writes.empty(),
                 "reject conflicting roles, rates, master setting bits and inconsistent status "
                 "fields")) {
      return false;
    }
  }
  Peer peer;
  peer.clocks[1] = status(rates[1], true);
  const auto result = octa::change_pair_clock(peer, rates[0]);
  return require(!result && result.error().cause.code == -EPERM && peer.writes.empty(),
                 "externally clocked EXP cannot be the rate-setting master");
}

bool changing_preflight() {
  constexpr std::array modes = {Fault::internal, Fault::inactive, Fault::rate, Fault::source_bits};
  for (const auto mode : modes) {
    Peer peer;
    peer.on_read = [mode](Peer &io, octa::PairUnit) {
      if (io.reads == 3) { fault(io.clocks[0], mode); }
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(!result && result.error().cause.code == -EAGAIN && peer.writes.empty(),
                 "clock changes between preflight snapshots abort without writes")) {
      return false;
    }
  }
  return true;
}

bool unmapped_status() {
  Peer peer;
  peer.on_read = [](Peer &io, octa::PairUnit unit) {
    auto &clock = io.clocks[unit == octa::PairUnit::base ? 0 : 1];
    clock.raw[1] ^= 1;
    clock.raw[2] ^= 2;
    clock.raw[7] = clock.raw[7] == 1 ? 2 : 1;
  };
  const auto result = octa::change_pair_clock(peer, rates[0]);
  if (!require(result && result->changed && peer.pauses == 1,
               "unmapped bytes and nonzero activation encodings do not destabilize snapshots")) {
    return false;
  }
  Peer inactive;
  fault(inactive.clocks[1], Fault::inactive);
  return require(octa::change_pair_clock(inactive, rates[1]).has_value(),
                 "internal EXP activation flag is not an external synchronization requirement");
}

bool bounded_settling() {
  for (unsigned int busy = 0; busy < polls; ++busy) {
    Peer peer;
    peer.on_prepare = [](Peer &io, std::uint32_t rate) -> Reply {
      io.clocks[1] = status(rate, false);
      if (io.writes.size() == 2) { io.set_rate(rate); }
      return {};
    };
    peer.on_read = [busy](Peer &io, octa::PairUnit) {
      if (io.writes.size() == 1 && io.reads >= 5 + (busy * 2)) { io.set_rate(rates[0]); }
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (busy + 2 <= polls) {
      if (!require(result && result->changed && peer.writes == std::vector{rates[0]} &&
                       peer.pauses == busy + 1 && peer.reads == 4 + ((busy + 2) * 2),
                   "settling accepts exactly two consecutive ready snapshots within forty polls")) {
        return false;
      }
    } else if (!require(!result && result.error().cause.code == -ETIMEDOUT &&
                            result.error().change_attempted && !result.error().recovery &&
                            peer.writes == std::vector{rates[0], rates[1]} &&
                            peer.pauses == polls && peer.clocks[0].sample_rate_hz == rates[1],
                        "one final ready snapshot times out and restores the previous pair rate")) {
      return false;
    }
  }
  return true;
}

bool prepare_failures() {
  for (const auto error : {-EIO, -ENODEV, -EACCES, -ETIMEDOUT}) {
    Peer peer;
    peer.on_prepare = [error](Peer &io, std::uint32_t rate) -> Reply {
      io.set_rate(rate);
      if (io.writes.size() == 1) {
        return std::unexpected(
            octa::StatusReadError{.code = error, .operation = "test uncertain prepare"});
      }
      return {};
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(!result && result.error().cause.code == error && result.error().change_attempted &&
                     !result.error().recovery && peer.writes == std::vector{rates[0], rates[1]} &&
                     peer.reads == 9 && peer.clocks[0].sample_rate_hz == rates[1],
                 "prepare errors may have changed hardware and require verified recovery")) {
      return false;
    }
  }
  return true;
}

bool transient_reads() {
  for (const auto error : {-EIO, -EPROTO, -EAGAIN}) {
    Peer peer;
    peer.on_read = [error](Peer &io, octa::PairUnit) {
      io.read_error.reset();
      if (io.reads == 7) { io.read_error = {.code = error, .operation = "test transient read"}; }
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(result && result->changed && peer.writes.size() == 1 && peer.reads == 11 &&
                     peer.pauses == 3,
                 "a transient read breaks consecutive snapshots and can recover without a write")) {
      return false;
    }
  }
  return true;
}

bool fatal_reads() {
  for (const auto error : {-ENODEV, -EACCES}) {
    for (const auto unit : {octa::PairUnit::base, octa::PairUnit::expansion}) {
      Peer peer;
      peer.on_read = [error, unit](Peer &io, octa::PairUnit current) {
        io.read_error.reset();
        if (io.writes.size() == 1 && current == unit) {
          io.read_error = {.code = error, .operation = "test disconnected unit"};
        }
      };
      const auto result = octa::change_pair_clock(peer, rates[0]);
      const bool base = unit == octa::PairUnit::base;
      if (!require(
              !result && result.error().cause.code == error && result.error().change_attempted &&
                  result.error().recovery.has_value() == !base &&
                  peer.writes == (base ? std::vector{rates[0], rates[1]} : std::vector{rates[0]}),
              "lost base still permits master restoration; lost EXP cannot be written")) {
        return false;
      }
    }
  }
  return true;
}

bool changed_recovery_master() {
  for (const auto mode : {Fault::source_bits, Fault::typed_rate}) {
    Peer peer;
    peer.on_prepare = [mode](Peer &io, std::uint32_t rate) -> Reply {
      io.set_rate(rate);
      fault(io.clocks[1], mode);
      return std::unexpected(octa::StatusReadError{.code = -EIO, .operation = "test prepare"});
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(!result && result.error().cause.code == -EIO && recovery_is(result, -EPERM) &&
                     peer.writes == std::vector{rates[0]},
                 "recovery refuses a changed or inconsistently decoded master")) {
      return false;
    }
  }
  return true;
}

bool failed_recovery() {
  Peer peer;
  peer.on_prepare = [](Peer &io, std::uint32_t rate) -> Reply {
    io.set_rate(rate);
    return std::unexpected(octa::StatusReadError{.code = io.writes.size() == 1 ? -EIO : -ENODEV,
                                                 .operation = "test prepare failure"});
  };
  const auto result = octa::change_pair_clock(peer, rates[0]);
  if (!require(!result && result.error().cause.code == -EIO && recovery_is(result, -ENODEV) &&
                   result.error().change_attempted &&
                   peer.writes == std::vector{rates[0], rates[1]},
               "failed recovery remains distinct from the original error")) {
    return false;
  }
  Peer timeout;
  timeout.on_prepare = [](Peer &io, std::uint32_t rate) -> Reply {
    io.clocks[1] = status(rate, false);
    io.clocks[0] = status(rates[2], true);
    return {};
  };
  const auto timed = octa::change_pair_clock(timeout, rates[0]);
  return require(
      !timed && timed.error().cause.code == -ETIMEDOUT && recovery_is(timed, -ETIMEDOUT) &&
          timeout.writes == std::vector{rates[0], rates[1]} && timeout.pauses == (polls - 1) * 2,
      "recovery itself is bounded and cannot report an unverified restoration");
}

bool cancellation() {
  for (const auto after_pause : {false, true}) {
    Peer peer;
    peer.on_read = [after_pause](Peer &io, octa::PairUnit) {
      if (!after_pause && io.reads == 6) { io.cancelled_flag = true; }
    };
    peer.on_pause = [after_pause](Peer &io) {
      if (after_pause) { io.cancelled_flag = true; }
    };
    const auto result = octa::change_pair_clock(peer, rates[0]);
    if (!require(!result && result.error().cause.code == -EINTR &&
                     result.error().change_attempted && !result.error().recovery &&
                     peer.writes == std::vector{rates[0], rates[1]} &&
                     peer.clocks[0].sample_rate_hz == rates[1],
                 "cancellation after mutation restores the original rate despite the signal")) {
      return false;
    }
  }
  return true;
}

bool deadlines_and_stability() {
  Peer slow;
  slow.read_time = std::chrono::seconds(4);
  const auto timed = octa::change_pair_clock(slow, rates[0]);
  if (!require(!timed && timed.error().cause.code == -ETIMEDOUT && recovery_is(timed, -ETIMEDOUT) &&
                   slow.pauses == 0,
               "slow status reads cannot commit after the settling deadline")) {
    return false;
  }
  Peer unstable;
  unstable.on_read = [](Peer &io, octa::PairUnit unit) {
    if (io.writes.size() == 1 && unit == octa::PairUnit::base) { io.clocks[0].raw[6] ^= 2; }
  };
  const auto changed = octa::change_pair_clock(unstable, rates[0]);
  return require(!changed && changed.error().cause.code == -ETIMEDOUT && !changed.error().recovery,
                 "changing source bits prevent settling even if decoded source and rate match");
}

}

int main() {
  if (!successful_rates() || !invalid_requests() || !preflight_errors() || !preflight_roles() ||
      !changing_preflight() || !unmapped_status() || !bounded_settling() || !prepare_failures() ||
      !transient_reads() || !fatal_reads() || !changed_recovery_master() || !failed_recovery() ||
      !cancellation() || !deadlines_and_stability()) {
    return 1;
  }
  std::cout
      << "PASS paired clock rates, roles, bounded settling, cancellation and verified recovery; "
         "scripted transport only, no hardware access\n";
}
