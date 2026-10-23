if(NOT DEFINED INPUT OR NOT DEFINED OUTPUT)
    message(FATAL_ERROR "embed_bios.cmake requires INPUT and OUTPUT")
endif()

file(READ "${INPUT}" BIOS_HEX HEX)
file(SHA256 "${INPUT}" BIOS_SHA256)
if(NOT BIOS_SHA256 STREQUAL "9112306f2d1ef29281604d7ee3b3e816b2b9cff04a93a4174f5d8181f06fd5da")
    message(FATAL_ERROR "The built-in HLE BIOS checksum is not the reviewed image")
endif()
string(LENGTH "${BIOS_HEX}" BIOS_HEX_LENGTH)
if(NOT BIOS_HEX_LENGTH EQUAL 131072)
    message(FATAL_ERROR "The built-in HLE BIOS must be exactly 65536 bytes")
endif()

string(REGEX REPLACE "([0-9a-fA-F][0-9a-fA-F])" "0x\\1," BIOS_BYTES "${BIOS_HEX}")
get_filename_component(OUTPUT_DIR "${OUTPUT}" DIRECTORY)
file(MAKE_DIRECTORY "${OUTPUT_DIR}")
file(WRITE "${OUTPUT}"
"#pragma once\n"
"#include <cstddef>\n"
"namespace ngpcraft_firmware {\n"
"inline constexpr unsigned char bios_hle[] = {${BIOS_BYTES}};\n"
"inline constexpr std::size_t bios_hle_size = sizeof(bios_hle);\n"
"static_assert(bios_hle_size == 65536, \"invalid embedded HLE BIOS size\");\n"
"}\n")
