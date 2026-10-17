// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeterControl.hpp"
#include "octa/PcmMeters.hpp"
#include "octa/StatusControl.hpp"

#include <charconv>
#include <chrono>
#include <iostream>
#include <string_view>
#include <system_error>
#include <thread>

extern "C" {
#include <signal.h>
}

namespace {

volatile sig_atomic_t interrupted = 0;

void interrupt(int /*signal*/) {
  interrupted = 1;
}

bool handlers() {
  struct sigaction action{};
  action.sa_handler = &interrupt;
  if (sigemptyset(&action.sa_mask) != 0 || sigaction(SIGINT, &action, nullptr) != 0 ||
      sigaction(SIGTERM, &action, nullptr) != 0) {
    return false;
  }
  action.sa_handler = SIG_IGN;
  return sigaction(SIGPIPE, &action, nullptr) == 0;
}

int failure(octa::StatusReadError error) {
  std::cerr << error.operation << ": ALSA error " << error.code << '\n';
  return 1;
}

}

int main(int argc, char **argv) {
  constexpr int maximum_seconds = 60;
  constexpr auto interval = std::chrono::milliseconds(30);
  int seconds = 1;
  const std::string_view argument = argc >= 2 ? argv[1] : "1";
  const bool passive = argument == "--read";
  const auto parsed = std::from_chars(argument.begin(), argument.end(), seconds);
  if (argc > 3 || (!passive && (parsed.ec != std::errc{} || parsed.ptr != argument.end() ||
                                seconds < 1 || seconds > maximum_seconds))) {
    std::cerr << "Usage: octa-pcm-meters [seconds:1..60|--read] [hw:CARD=OCTACAPTURE]\n";
    return 2;
  }
  if (!handlers()) { return 1; }
  auto control = octa::PcmMeterControl::open(argc == 3 ? argv[2] : "hw:CARD=OCTACAPTURE");
  if (!control) { return failure(control.error()); }
  if (!passive) {
    const auto enabled = control->enable(true);
    if (!enabled) { return failure(enabled.error()); }
  }
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
  do {
    const auto meters = control->read();
    if (!meters) { return failure(meters.error()); }
    octa::write_pcm_meters_json(std::cout, *meters);
    std::cout << '\n' << std::flush;
    if (!std::cout.good()) { return 1; }
    if (passive) { return 0; }
    std::this_thread::sleep_for(interval);
  } while (interrupted == 0 && std::chrono::steady_clock::now() < deadline);
  const auto stopped = control->enable(false);
  return stopped ? 0 : failure(stopped.error());
}
