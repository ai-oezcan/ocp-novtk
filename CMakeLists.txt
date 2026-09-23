cmake_minimum_required(VERSION 3.29)
project(OCP)

set(N_PROC
    2
    CACHE STRING "Number of processes used for generating code")
set(MSVC_VER
    16
    CACHE STRING "MSVC version to download for win generation")
set(OCCT_LIB_DIR "" CACHE PATH "Windows OCCT DLL directory for cross-generation")
set(DLL_EXT dll CACHE STRING "Windows OCCT DLL suffix for cross-generation")
option(OCP_ENABLE_VTK "Generate VTK bindings" OFF)
if(OCP_ENABLE_VTK)
  message(FATAL_ERROR "This repository only generates VTK-free OCP bindings")
endif()

if(NOT DEFINED ENV{CONDA_PREFIX} OR "$ENV{CONDA_PREFIX}" STREQUAL "")
  message(FATAL_ERROR "Activate the conda environment containing occt=8.0.1=novtk_*")
endif()

find_package(OpenCASCADE 8.0.1 CONFIG REQUIRED
             PATHS "$ENV{CONDA_PREFIX}" "$ENV{CONDA_PREFIX}/Library"
             NO_DEFAULT_PATH)
find_package(LLVM REQUIRED)
find_package(RapidJSON REQUIRED)
find_package(Clang REQUIRED)

set(Python_FIND_VIRTUALENV FIRST)
find_package(
  Python
  COMPONENTS Interpreter Development
  REQUIRED)

# Native generation dumps symbols from the imported shared OCCT targets that
# the generated module links against. Cross-generation of Windows bindings on
# Linux instead dumps the target conda package's DLLs, not the host's .so files.
set(occt_libs)
if(OCCT_LIB_DIR)
  if(NOT PLATFORM STREQUAL "Windows")
    message(FATAL_ERROR "OCCT_LIB_DIR is only for Windows cross-generation")
  endif()
  file(GLOB occt_libs LIST_DIRECTORIES false "${OCCT_LIB_DIR}/TK*.${DLL_EXT}")
  if(NOT occt_libs)
    message(FATAL_ERROR "No OCCT DLLs found in ${OCCT_LIB_DIR}")
  endif()
else()
  if(NOT OpenCASCADE_LIBRARIES)
    message(FATAL_ERROR "OpenCASCADE exports no OCCT libraries")
  endif()
  foreach(target IN LISTS OpenCASCADE_LIBRARIES)
    if(NOT TARGET ${target})
      message(FATAL_ERROR "OpenCASCADE library ${target} is not an imported target")
    endif()
    get_target_property(is_imported ${target} IMPORTED)
    get_target_property(target_type ${target} TYPE)
    if(NOT is_imported OR NOT target_type STREQUAL "SHARED_LIBRARY")
      message(FATAL_ERROR "OpenCASCADE library ${target} is not an imported shared target")
    endif()
    # Windows IMPORTED_LOCATION is the DLL, not the import library.
    get_target_property(loc ${target} IMPORTED_LOCATION_RELEASE)
    if(NOT loc OR loc MATCHES "-NOTFOUND$")
      get_target_property(loc ${target} IMPORTED_LOCATION)
    endif()
    if(NOT loc OR loc MATCHES "-NOTFOUND$" OR NOT EXISTS "${loc}")
      message(FATAL_ERROR "No shared OCCT library for ${target}: ${loc}")
    endif()
    list(APPEND occt_libs "${loc}")
  endforeach()
endif()

execute_process(
  COMMAND ${Python_EXECUTABLE} ${CMAKE_SOURCE_DIR}/dump_symbols.py "${occt_libs}"
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  COMMAND_ERROR_IS_FATAL ANY)

get_target_property(LIBCLANG_PATH libclang IMPORTED_LOCATION_RELEASE)
message(STATUS "Include dirs: ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES}")

# OSX
if(${CMAKE_SYSTEM_NAME} STREQUAL "Darwin")
  list(APPEND CXX_INCLUDES -i /opt/usr/local/include/c++/v1/ -i
       /opt/usr/local/include/)

# Linux-like (including FreeBSD)
elseif(NOT PLATFORM STREQUAL "Windows")
  foreach(inc IN LISTS CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES)
    list(APPEND CXX_INCLUDES -i ${inc}/)
  endforeach()

# X-generation from Linux to Windows
else()
  # download part of msvc 16 and winsdk
  if(NOT EXISTS ${CMAKE_BINARY_DIR}/msvc)
    execute_process(
      COMMAND
        ${Python_EXECUTABLE} ${CMAKE_SOURCE_DIR}/extern/msvc-wine/vsdownload.py
        --major ${MSVC_VER} --accept-license --dest msvc Win10SDK_10.0.19041
        Microsoft.VisualCpp.CRT.Headers
      WORKING_DIRECTORY ${CMAKE_BINARY_DIR})
  endif()

  list(APPEND CXX_INCLUDES -i
       ${CMAKE_BINARY_DIR}/msvc/VC/Tools/MSVC/14.29.30133/include)
  list(APPEND CXX_INCLUDES -i
       ${CMAKE_BINARY_DIR}/msvc/Windows\ Kits/10/Include/10.0.19041.0/ucrt/)
  list(APPEND CXX_INCLUDES -i ${CMAKE_SOURCE_DIR}/extern/include/)
endif()

if(PLATFORM)
  message(STATUS "Requested platform: ${PLATFORM}")
else()
  message(STATUS "Platform not specified.")

  if(${CMAKE_SYSTEM_NAME} STREQUAL "Windows")
      set(PLATFORM Windows)
  elseif(${CMAKE_SYSTEM_NAME} STREQUAL "Darwin")
      set(PLATFORM OSX)
  elseif(${CMAKE_SYSTEM_NAME} STREQUAL "FreeBSD")
      set(PLATFORM FreeBSD)
  else()
      set(PLATFORM Linux)
  endif()
endif()

set(ENV{PYTHONPATH} ${CMAKE_SOURCE_DIR}/pywrap)

add_custom_command(
  OUTPUT ${CMAKE_BINARY_DIR}/tmp.pkl
  COMMAND
    ${CMAKE_COMMAND} -E env "PYTHONPATH=${CMAKE_SOURCE_DIR}/pywrap"
    ${Python_EXECUTABLE} -m bindgen -n ${N_PROC} -l ${LIBCLANG_PATH}
    -i ${RapidJSON_INCLUDE_DIRS} -i
    ${CLANG_INSTALL_PREFIX}/lib/clang/${LLVM_VERSION_MAJOR}/include/ -p
    ${CMAKE_SOURCE_DIR} ${CXX_INCLUDES} parse ${CMAKE_SOURCE_DIR}/ocp.toml
    tmp.pkl ${PLATFORM}
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  DEPENDS ${CMAKE_SOURCE_DIR}/ocp.toml
  USES_TERMINAL
  COMMENT "Running pywrap - parse")

add_custom_target(pywrap_parse ALL DEPENDS ${CMAKE_BINARY_DIR}/tmp.pkl)

add_custom_command(
  OUTPUT ${CMAKE_BINARY_DIR}/tmp_filtered.pkl
  COMMAND
    ${CMAKE_COMMAND} -E env "PYTHONPATH=${CMAKE_SOURCE_DIR}/pywrap"
    ${Python_EXECUTABLE} -m bindgen -n ${N_PROC} -l ${LIBCLANG_PATH}
    -i ${RapidJSON_INCLUDE_DIRS} -i
    ${CLANG_INSTALL_PREFIX}/lib/clang/${LLVM_VERSION_MAJOR}/include/ -p
    ${CMAKE_SOURCE_DIR} ${CXX_INCLUDES} transform ${CMAKE_SOURCE_DIR}/ocp.toml
    ${PLATFORM} tmp.pkl tmp_filtered.pkl
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  DEPENDS ${CMAKE_BINARY_DIR}/tmp.pkl
  USES_TERMINAL
  COMMENT "Running pywrap - transform")

add_custom_target(pywrap_transform ALL
                  DEPENDS ${CMAKE_BINARY_DIR}/tmp_filtered.pkl)

add_custom_command(
  OUTPUT ${CMAKE_BINARY_DIR}/OCP/CMakeLists.txt
  COMMAND
    ${CMAKE_COMMAND} -E env "PYTHONPATH=${CMAKE_SOURCE_DIR}/pywrap"
    ${Python_EXECUTABLE} -m bindgen -n ${N_PROC} -l ${LIBCLANG_PATH}
    -i ${RapidJSON_INCLUDE_DIRS} -i
    ${CLANG_INSTALL_PREFIX}/lib/clang/${LLVM_VERSION_MAJOR}/include/ -p
    ${CMAKE_SOURCE_DIR} ${CXX_INCLUDES} generate ${CMAKE_SOURCE_DIR}/ocp.toml
    ${PLATFORM} tmp_filtered.pkl
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  DEPENDS ${CMAKE_BINARY_DIR}/tmp_filtered.pkl
  USES_TERMINAL
  COMMENT "Running pywrap - generate")

add_custom_target(pywrap_generate ALL
                  DEPENDS ${CMAKE_BINARY_DIR}/OCP/CMakeLists.txt)
