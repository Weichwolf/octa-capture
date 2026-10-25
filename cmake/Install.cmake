# SPDX-License-Identifier: GPL-2.0-only
include(GNUInstallDirs)

install(TARGETS octa-auto-sense octa-preset octa-probe octa-pcm-meters
  octa-pcm-peak-check octa-status octa-audio-check octa-session-check
  octa-meters octa-panel octa-state octa-bridge octa-dsp octa-control
  RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT Runtime)

file(RELATIVE_PATH OCTA_GUI_RELATIVE "${CMAKE_INSTALL_FULL_BINDIR}"
  "${CMAKE_INSTALL_FULL_LIBEXECDIR}/octa-capture")
foreach(OCTA_ENTRY IN ITEMS desktop server pulse_audio aggregate)
  set(OCTA_GUI_MODULE "gui.${OCTA_ENTRY}")
  if(OCTA_ENTRY STREQUAL "desktop")
    set(OCTA_LAUNCHER octa-capture)
  elseif(OCTA_ENTRY STREQUAL "server")
    set(OCTA_LAUNCHER octa-capture-http)
  elseif(OCTA_ENTRY STREQUAL "pulse_audio")
    set(OCTA_LAUNCHER octa-pulse-pairs)
  else()
    set(OCTA_LAUNCHER octa-aggregate)
  endif()
  configure_file("${CMAKE_SOURCE_DIR}/cmake/GuiLauncher.py.in"
    "${CMAKE_BINARY_DIR}/launchers/${OCTA_LAUNCHER}" @ONLY)
  install(PROGRAMS "${CMAKE_BINARY_DIR}/launchers/${OCTA_LAUNCHER}"
    DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT Runtime)
endforeach()

install(DIRECTORY "${CMAKE_SOURCE_DIR}/gui"
  DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/octa-capture" COMPONENT Runtime
  FILES_MATCHING PATTERN "*.py" PATTERN "*.js" PATTERN "*.html" PATTERN "*.css" PATTERN "*.svg"
  PATTERN "__pycache__" EXCLUDE)
configure_file("${CMAKE_SOURCE_DIR}/cmake/InstallDesktop.cmake.in"
  "${CMAKE_BINARY_DIR}/InstallDesktop.cmake" @ONLY)
install(SCRIPT "${CMAKE_BINARY_DIR}/InstallDesktop.cmake" COMPONENT Runtime)
install(FILES "${CMAKE_SOURCE_DIR}/gui/web/favicon.svg"
  DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/icons/hicolor/scalable/apps" RENAME octa-capture.svg COMPONENT Runtime)
install(FILES "${CMAKE_SOURCE_DIR}/LICENSE"
  DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/doc/octa-capture" RENAME copyright COMPONENT Runtime)
install(FILES "${CMAKE_SOURCE_DIR}/README.md"
  DESTINATION "${CMAKE_INSTALL_DATAROOTDIR}/doc/octa-capture" COMPONENT Runtime)

set(OCTA_MAN_PROGRAMS octa-capture octa-capture-http octa-aggregate octa-pulse-pairs
  octa-control octa-dsp octa-state octa-status octa-probe octa-panel octa-meters
  octa-pcm-meters octa-pcm-peak-check octa-audio-check octa-session-check
  octa-auto-sense octa-preset octa-bridge)
find_program(OCTA_GZIP NAMES gzip REQUIRED)
foreach(OCTA_MAN_PROGRAM IN LISTS OCTA_MAN_PROGRAMS)
  configure_file("${CMAKE_SOURCE_DIR}/packaging/man/${OCTA_MAN_PROGRAM}.1.in"
    "${CMAKE_BINARY_DIR}/man/${OCTA_MAN_PROGRAM}.1" @ONLY)
  execute_process(COMMAND "${OCTA_GZIP}" -n -9 -c "${CMAKE_BINARY_DIR}/man/${OCTA_MAN_PROGRAM}.1"
    OUTPUT_FILE "${CMAKE_BINARY_DIR}/man/${OCTA_MAN_PROGRAM}.1.gz" COMMAND_ERROR_IS_FATAL ANY)
  install(FILES "${CMAKE_BINARY_DIR}/man/${OCTA_MAN_PROGRAM}.1.gz"
    DESTINATION "${CMAKE_INSTALL_MANDIR}/man1" COMPONENT Runtime)
endforeach()

set(CPACK_PACKAGE_NAME octa-capture)
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_VENDOR "OCTA-CAPTURE contributors")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "OCTA-CAPTURE Linux control panel and device tools")
set(CPACK_PACKAGE_HOMEPAGE_URL "https://github.com/Weichwolf/octa-capture")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_SOURCE_DIR}/LICENSE")
set(CPACK_GENERATOR DEB)
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_DEBIAN_PACKAGE_MAINTAINER "OCTA-CAPTURE contributors")
set(CPACK_DEBIAN_PACKAGE_SECTION sound)
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
set(CPACK_DEBIAN_PACKAGE_DEPENDS "python3, python3-gi, python3-xlib, gir1.2-gtk-4.0 (>= 4.10), gir1.2-webkit-6.0, fonts-liberation")
set(CPACK_SOURCE_GENERATOR TXZ)
set(CPACK_SOURCE_IGNORE_FILES
  "/[.]git/;/build/;/doc/local/;/doc/[^/]*[.](zip|tgz)$;/__pycache__/;[.]pyc$;/compile_commands[.]json$"
  "/doc/check-[^/]*[.]txt$;/doc/kernel-config[.]txt$;/doc/tools-inventory[.]json$;/doc/roland-[^/]*-download[.]html$")
include(CPack)
