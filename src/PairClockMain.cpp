// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairClock.hpp"
#include "octa/PairClockNative.hpp"

#include <array>
#include <charconv>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <system_error>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t cancelled = 0;

extern "C" void cancel(int /*signal*/) {
  cancelled = 1;
}

int failure(const octa::PairClockFailure &error) {
  std::cerr << error.cause.operation << ": ALSA/system error " << error.cause.code << '\n';
  if (error.recovery) {
    std::cerr << "Recovery failed: " << error.recovery->operation << ": " << error.recovery->code
              << '\n';
  }
  std::cout << R"({"ok":false,"error":)" << error.cause.code << R"(,"change_attempted":)"
            << std::boolalpha << error.change_attempted << R"(,"recovered":)";
  if (error.change_attempted) {
    std::cout << !error.recovery.has_value();
  } else {
    std::cout << "null";
  }
  std::cout << "}\n";
  return 1;
}

}

int main(int argc, char **argv) {
  constexpr std::string_view usage =
      "Usage: octa-pair-clock --experimental RATE BASE_CARD BASE_SERIAL EXP_CARD EXP_SERIAL\n"
      "Both audio servers/clients must release both units. Does not start audio.\n";
  if (argc == 2 && std::string_view(argv[1]) == "--help") {
    std::cout << usage;
    return 0;
  }
  constexpr int arguments = 7;
  if (argc != arguments || std::string_view(argv[1]) != "--experimental" || geteuid() == 0) {
    std::cerr << usage << "Run as the regular device user.\n";
    return 2;
  }
  const std::string_view text(argv[2]);
  std::uint32_t rate = 0;
  const auto parsed = std::from_chars(text.data(), text.data() + text.size(), rate);
  if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
    std::cerr << usage;
    return 2;
  }
  if (std::signal(SIGINT, cancel) == SIG_ERR || std::signal(SIGTERM, cancel) == SIG_ERR) {
    std::cerr << "Cannot install clock transaction cancellation handlers.\n";
    return 1;
  }
  const std::array identities = {octa::PairIdentity{.card = argv[3], .serial = argv[4]},
                                 octa::PairIdentity{.card = argv[5], .serial = argv[6]}};
  const auto result = octa::change_native_pair_clock(identities, rate, cancelled);
  if (!result) { return failure(result.error()); }
  std::cout << R"({"ok":true,"experimental":true,"previous_rate_hz":)" << result->previous_rate
            << R"(,"rate_hz":)" << result->rate << R"(,"changed":)" << std::boolalpha
            << result->changed << "}\n"
            << std::flush;
  return std::cout.good() ? 0 : 1;
}
