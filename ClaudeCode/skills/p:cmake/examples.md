# CMake Examples

Extended examples demonstrating various CMake patterns and use cases.

## Example 1: Full imgcat2-style Multi-Format Image Tool

Complete real-world example based on imgcat2 project:

```cmake
cmake_minimum_required( VERSION 3.15 )
project( imgcat2 VERSION 1.0.0 LANGUAGES C )

# Set default build type
if( NOT CMAKE_BUILD_TYPE )
	set( CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Choose the type of build" FORCE )
endif()

set( CMAKE_EXPORT_COMPILE_COMMANDS ON )
set( CMAKE_C_STANDARD 11 )
set( CMAKE_C_STANDARD_REQUIRED ON )
set( CMAKE_C_EXTENSIONS ON )  # GNU extensions for gnu11

# Build options
option( ENABLE_GIF "Enable GIF support ( giflib )" ON )
option( ENABLE_WEBP "Enable WebP support" ON )
option( ENABLE_HEIF "Enable HEIF support" ON )
option( ENABLE_TIFF "Enable TIFF support" ON )
option( BUILD_TESTING "Build tests" ON )

# Static linking preference
set( BUILD_SHARED_LIBS OFF CACHE BOOL "Build shared libraries" FORCE )

# Platform-specific static linking
# macOS doesn't support fully static binaries ( libSystem must be dynamic )
# Only link third-party libraries statically on macOS
if( UNIX AND NOT APPLE )
	# Linux: Full static linking
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )
	message( STATUS "Linux: Building fully static binary" )
elseif( APPLE )
	# macOS: Static link third-party libs, dynamic system libs
	# Only search for .a files to force static linking
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	message( STATUS "macOS: Building with static third-party libraries" )
endif()

# Compiler flags
add_compile_options( -Wall -Wextra -Werror -O3 -g )

# Stack size linker flags
if( UNIX AND NOT APPLE )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-z,stack-size=8388608" )
elseif( APPLE )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-stack_size,0x800000" )
endif()

# pkg-config
find_package( PkgConfig REQUIRED )

# zlib ( REQUIRED )
if( APPLE )
	# On macOS, find static library explicitly
	find_library( ZLIB_LIBRARY NAMES z REQUIRED
		HINTS /usr/local/opt/zlib/lib /usr/local/lib /opt/homebrew/opt/zlib/lib )
	find_path( ZLIB_INCLUDE_DIR zlib.h
		HINTS /usr/local/opt/zlib/include /usr/local/include /opt/homebrew/opt/zlib/include )
	if( ZLIB_LIBRARY AND ZLIB_INCLUDE_DIR )
		set( ZLIB_LIBRARIES ${ZLIB_LIBRARY} )
		set( ZLIB_INCLUDE_DIRS ${ZLIB_INCLUDE_DIR} )
		set( ZLIB_FOUND TRUE )
	endif()
else()
	pkg_check_modules( ZLIB REQUIRED zlib )
endif()

# libpng ( REQUIRED )
if( APPLE )
	find_library( PNG_LIBRARY NAMES png16 png REQUIRED
		HINTS /usr/local/opt/libpng/lib /opt/homebrew/opt/libpng/lib )
	find_path( PNG_INCLUDE_DIR png.h PATH_SUFFIXES libpng16 libpng
		HINTS /usr/local/opt/libpng/include /opt/homebrew/opt/libpng/include )
	if( PNG_LIBRARY AND PNG_INCLUDE_DIR )
		set( PNG_LIBRARIES ${PNG_LIBRARY} )
		set( PNG_INCLUDE_DIRS ${PNG_INCLUDE_DIR} )
		set( PNG_FOUND TRUE )
	endif()
else()
	pkg_check_modules( PNG REQUIRED libpng16 )
	if( NOT PNG_FOUND )
		pkg_check_modules( PNG REQUIRED libpng )
	endif()
endif()

if( NOT PNG_FOUND )
	message( FATAL_ERROR "libpng is required but not found" )
endif()

# libjpeg-turbo ( REQUIRED )
pkg_check_modules( JPEG libjpeg-turbo )
if( NOT JPEG_FOUND )
	find_package( JPEG REQUIRED )
	set( JPEG_LIBRARIES ${JPEG_LIBRARIES} )
	set( JPEG_INCLUDE_DIRS ${JPEG_INCLUDE_DIRS} )
endif()

# giflib ( OPTIONAL )
if( ENABLE_GIF )
	find_library( GIF_LIBRARY NAMES gif )
	find_path( GIF_INCLUDE_DIR gif_lib.h )
	if( GIF_LIBRARY AND GIF_INCLUDE_DIR )
		set( GIF_FOUND TRUE )
		message( STATUS "GIF support: ENABLED" )
	else()
		message( WARNING "giflib not found, GIF support disabled" )
		set( GIF_FOUND FALSE )
		set( ENABLE_GIF OFF )
	endif()
endif()

# WebP ( OPTIONAL )
if( ENABLE_WEBP )
	if( APPLE )
		find_library( WEBP_LIBRARY NAMES webp
			HINTS /usr/local/opt/webp/lib /opt/homebrew/opt/webp/lib )
		find_path( WEBP_INCLUDE_DIR webp/decode.h
			HINTS /usr/local/opt/webp/include /opt/homebrew/opt/webp/include )
		if( WEBP_LIBRARY AND WEBP_INCLUDE_DIR )
			set( WEBP_LIBRARIES ${WEBP_LIBRARY} )
			set( WEBP_INCLUDE_DIRS ${WEBP_INCLUDE_DIR} )
			set( WEBP_FOUND TRUE )
		endif()
	else()
		pkg_check_modules( WEBP libwebp )
	endif()

	if( WEBP_FOUND )
		message( STATUS "WebP support: ENABLED" )
	else()
		message( WARNING "libwebp not found, WebP support disabled" )
		set( ENABLE_WEBP OFF )
	endif()
endif()

# HEIF ( OPTIONAL )
if( ENABLE_HEIF )
	pkg_check_modules( HEIF libheif )
	if( HEIF_FOUND )
		message( STATUS "HEIF support: ENABLED" )
	else()
		message( WARNING "libheif not found, HEIF support disabled" )
		set( ENABLE_HEIF OFF )
	endif()
endif()

# TIFF ( OPTIONAL )
if( ENABLE_TIFF )
	find_package( TIFF )
	if( TIFF_FOUND )
		message( STATUS "TIFF support: ENABLED" )
	else()
		message( WARNING "libtiff not found, TIFF support disabled" )
		set( ENABLE_TIFF OFF )
	endif()
endif()

# Source files
set( IMGCAT2_SOURCES
	src/imgcat2/core/image.c
	src/imgcat2/core/pipeline.c
	src/imgcat2/core/cli.c
	src/imgcat2/decoders/decoder.c
	src/imgcat2/decoders/decoder_png.c
	src/imgcat2/decoders/decoder_jpeg.c
	src/imgcat2/ansi/ansi.c
	src/imgcat2/ansi/escape.c
 )

if( GIF_FOUND )
	list( APPEND IMGCAT2_SOURCES src/imgcat2/decoders/decoder_gif.c )
endif()

# Platform-specific terminal module
if( WIN32 )
	list( APPEND IMGCAT2_SOURCES src/imgcat2/terminal/terminal_windows.c )
else()
	list( APPEND IMGCAT2_SOURCES src/imgcat2/terminal/terminal_unix.c )
endif()

# Main executable
add_executable( imgcat2 ${IMGCAT2_SOURCES} src/imgcat2/main.c )

# Library for tests ( without main.c )
add_library( imgcat2_lib STATIC ${IMGCAT2_SOURCES} )

# Target-based include directories
target_include_directories( imgcat2 PRIVATE
	${CMAKE_SOURCE_DIR}/src
	${PNG_INCLUDE_DIRS}
	${ZLIB_INCLUDE_DIRS}
	${JPEG_INCLUDE_DIRS}
 )

target_include_directories( imgcat2_lib PUBLIC
	${CMAKE_SOURCE_DIR}/src
	${PNG_INCLUDE_DIRS}
	${ZLIB_INCLUDE_DIRS}
	${JPEG_INCLUDE_DIRS}
 )

# Target-based compile definitions
target_compile_definitions( imgcat2 PRIVATE
	HAVE_LIBPNG
	HAVE_LIBJPEG
 )

target_compile_definitions( imgcat2_lib PUBLIC
	HAVE_LIBPNG
	HAVE_LIBJPEG
 )

# Link libraries for executable
target_link_libraries( imgcat2 PRIVATE
	${PNG_LIBRARIES}
	${ZLIB_LIBRARIES}
	${JPEG_LIBRARIES}
 )

# Link libraries for library
target_link_libraries( imgcat2_lib PUBLIC
	${PNG_LIBRARIES}
	${ZLIB_LIBRARIES}
	${JPEG_LIBRARIES}
 )

if( GIF_FOUND )
	target_include_directories( imgcat2 PRIVATE ${GIF_INCLUDE_DIR} )
	target_include_directories( imgcat2_lib PUBLIC ${GIF_INCLUDE_DIR} )
	target_compile_definitions( imgcat2 PRIVATE HAVE_GIFLIB )
	target_compile_definitions( imgcat2_lib PUBLIC HAVE_GIFLIB )
	target_link_libraries( imgcat2 PRIVATE ${GIF_LIBRARY} )
	target_link_libraries( imgcat2_lib PUBLIC ${GIF_LIBRARY} )
endif()

if( WEBP_FOUND )
	target_include_directories( imgcat2 PRIVATE ${WEBP_INCLUDE_DIRS} )
	target_include_directories( imgcat2_lib PUBLIC ${WEBP_INCLUDE_DIRS} )
	target_compile_definitions( imgcat2 PRIVATE ENABLE_WEBP )
	target_compile_definitions( imgcat2_lib PUBLIC ENABLE_WEBP )
	target_link_libraries( imgcat2 PRIVATE ${WEBP_LIBRARIES} )
	target_link_libraries( imgcat2_lib PUBLIC ${WEBP_LIBRARIES} )
endif()

if( HEIF_FOUND )
	target_include_directories( imgcat2 PRIVATE ${HEIF_INCLUDE_DIRS} )
	target_include_directories( imgcat2_lib PUBLIC ${HEIF_INCLUDE_DIRS} )
	target_compile_definitions( imgcat2 PRIVATE ENABLE_HEIF )
	target_compile_definitions( imgcat2_lib PUBLIC ENABLE_HEIF )
	target_link_libraries( imgcat2 PRIVATE ${HEIF_LDFLAGS} )
	target_link_libraries( imgcat2_lib PUBLIC ${HEIF_LDFLAGS} )
endif()

if( TIFF_FOUND )
	target_include_directories( imgcat2 PRIVATE ${TIFF_INCLUDE_DIRS} )
	target_include_directories( imgcat2_lib PUBLIC ${TIFF_INCLUDE_DIRS} )
	target_compile_definitions( imgcat2 PRIVATE ENABLE_TIFF )
	target_compile_definitions( imgcat2_lib PUBLIC ENABLE_TIFF )
	target_link_libraries( imgcat2 PRIVATE ${TIFF_LIBRARIES} )
	target_link_libraries( imgcat2_lib PUBLIC ${TIFF_LIBRARIES} )
endif()

# Platform-specific libraries
if( UNIX AND NOT APPLE )
	target_link_libraries( imgcat2 PRIVATE m )
	target_link_libraries( imgcat2_lib PUBLIC m )
endif()

# Install target
install( TARGETS imgcat2 DESTINATION bin )

# Testing
if( BUILD_TESTING )
	enable_testing()
	add_subdirectory( src/tests )
endif()

# Print configuration summary
message( STATUS "" )
message( STATUS "imgcat2 Configuration Summary" )
message( STATUS "============================" )
message( STATUS "Version: ${PROJECT_VERSION}" )
message( STATUS "Build type: ${CMAKE_BUILD_TYPE}" )
message( STATUS "C compiler: ${CMAKE_C_COMPILER}" )
message( STATUS "C standard: C${CMAKE_C_STANDARD}" )
message( STATUS "" )
message( STATUS "Core Dependencies ( required ):" )
message( STATUS "  libpng: ${PNG_LIBRARIES}" )
message( STATUS "  zlib: ${ZLIB_LIBRARIES}" )
message( STATUS "  libjpeg: ${JPEG_LIBRARIES}" )
message( STATUS "" )
message( STATUS "Optional Format Support:" )
message( STATUS "  GIF:  ${ENABLE_GIF}" )
message( STATUS "  WebP: ${ENABLE_WEBP}" )
message( STATUS "  HEIF: ${ENABLE_HEIF}" )
message( STATUS "  TIFF: ${ENABLE_TIFF}" )
message( STATUS "" )
message( STATUS "Other Options:" )
message( STATUS "  Testing: ${BUILD_TESTING}" )
message( STATUS "" )
```

## Example 2: Simple Static CLI Tool

Minimal example for a simple command-line tool:

```cmake
cmake_minimum_required( VERSION 3.15 )
project( mycli VERSION 1.0.0 LANGUAGES C )

set( CMAKE_C_STANDARD 11 )
set( CMAKE_EXPORT_COMPILE_COMMANDS ON )

# Static linking
if( UNIX AND NOT APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )
elseif( APPLE )
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
endif()

# Simple dependency
find_package( PkgConfig REQUIRED )

if( APPLE )
	find_library( Z_LIB NAMES z REQUIRED
		HINTS /usr/local/opt/zlib/lib /opt/homebrew/opt/zlib/lib )
	set( LIBS ${Z_LIB} )
else()
	pkg_check_modules( Z REQUIRED zlib )
	set( LIBS ${Z_LIBRARIES} )
endif()

add_executable( mycli src/main.c )
target_link_libraries( mycli PRIVATE ${LIBS} )
target_compile_options( mycli PRIVATE -Wall -Wextra -O3 )

install( TARGETS mycli DESTINATION bin )
```

## Example 3: Cross-Platform Library

Library that builds as static or shared:

```cmake
cmake_minimum_required( VERSION 3.15 )
project( mylib VERSION 2.0.0 LANGUAGES C CXX )

option( BUILD_SHARED_LIBS "Build shared library" OFF )
option( BUILD_EXAMPLES "Build example programs" ON )

set( CMAKE_C_STANDARD 11 )
set( CMAKE_CXX_STANDARD 17 )

# Static linking for dependencies
if( NOT BUILD_SHARED_LIBS )
	if( UNIX AND NOT APPLE )
		set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	elseif( APPLE )
		set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	endif()
endif()

# Dependencies
find_package( PkgConfig REQUIRED )
pkg_check_modules( ZLIB REQUIRED zlib )

# Library
add_library( mylib
	src/core.c
	src/utils.c
	src/io.cpp
 )

target_include_directories( mylib
	PUBLIC
		$<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/include>
		$<INSTALL_INTERFACE:include>
	PRIVATE
		${CMAKE_SOURCE_DIR}/src
 )

target_link_libraries( mylib PUBLIC ${ZLIB_LIBRARIES} )
target_compile_features( mylib PUBLIC c_std_11 cxx_std_17 )

# Set library properties
set_target_properties( mylib PROPERTIES
	VERSION ${PROJECT_VERSION}
	SOVERSION 2
	PUBLIC_HEADER "include/mylib.h"
 )

# Example program
if( BUILD_EXAMPLES )
	add_executable( example examples/example.c )
	target_link_libraries( example PRIVATE mylib )
endif()

# Installation
include( GNUInstallDirs )
install( TARGETS mylib
	EXPORT mylibTargets
	LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
	ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
	RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
	PUBLIC_HEADER DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
 )

# Export targets
install( EXPORT mylibTargets
	FILE mylibTargets.cmake
	NAMESPACE mylib::
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/mylib
 )

# Config file
include( CMakePackageConfigHelpers )
write_basic_package_version_file(
	"mylibConfigVersion.cmake"
	VERSION ${PROJECT_VERSION}
	COMPATIBILITY SameMajorVersion
 )

install( FILES
	"mylibConfig.cmake"
	"${CMAKE_CURRENT_BINARY_DIR}/mylibConfigVersion.cmake"
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/mylib
 )
```

## Example 4: Cross-Compilation for ARM

```cmake
cmake_minimum_required( VERSION 3.15 )
project( embedded-app LANGUAGES C )

# Toolchain configuration
if( CMAKE_CROSSCOMPILING )
	message( STATUS "Cross-compiling for ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}" )

	# Force static linking for embedded
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )

	# Find libraries in sysroot
	set( CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER )
	set( CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY )
	set( CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY )
endif()

# Embedded-specific flags
if( CMAKE_SYSTEM_PROCESSOR MATCHES "arm" )
	add_compile_options(
		-mthumb
		-mcpu=cortex-m4
		-mfloat-abi=hard
		-mfpu=fpv4-sp-d16
	 )
endif()

add_executable( app src/main.c )
target_compile_options( app PRIVATE -Os -g )  # Optimize for size

# Link script for embedded
if( CMAKE_CROSSCOMPILING )
	target_link_options( app PRIVATE
		-T ${CMAKE_SOURCE_DIR}/linker/stm32f4.ld
		-Wl,-Map=output.map
	 )
endif()
```

**Toolchain file** ( `arm-toolchain.cmake` ):
```cmake
set( CMAKE_SYSTEM_NAME Linux )
set( CMAKE_SYSTEM_PROCESSOR arm )

set( CMAKE_C_COMPILER arm-none-eabi-gcc )
set( CMAKE_CXX_COMPILER arm-none-eabi-g++ )

set( CMAKE_FIND_ROOT_PATH /usr/arm-none-eabi )

set( CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER )
set( CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY )
set( CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY )
set( CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY )
```

**Usage**:
```bash
cmake -B build \
	-DCMAKE_TOOLCHAIN_FILE=arm-toolchain.cmake \
	-DCMAKE_BUILD_TYPE=Release
cmake --build build
```

## Example 5: Conditional Features with Auto-Detection

```cmake
cmake_minimum_required( VERSION 3.15 )
project( multimedia-app VERSION 1.0.0 LANGUAGES C )

# Auto-detect features
option( WITH_PNG "Enable PNG support (auto-detect if not specified)" AUTO )
option( WITH_JPEG "Enable JPEG support (auto-detect if not specified)" AUTO )
option( WITH_WEBP "Enable WebP support (auto-detect if not specified)" AUTO )

find_package( PkgConfig REQUIRED )

# Auto-detection function
function( detect_library VAR_PREFIX PKG_NAME LIBRARY_NAMES )
	set( OPTION_NAME "WITH_${VAR_PREFIX}" )

	if( ${OPTION_NAME} STREQUAL "AUTO" )
		message( STATUS "Auto-detecting ${VAR_PREFIX}..." )

		if( APPLE )
			find_library( ${VAR_PREFIX}_LIBRARY NAMES ${LIBRARY_NAMES}
				HINTS /usr/local/opt /opt/homebrew/opt
				PATH_SUFFIXES lib
			)
			if( ${VAR_PREFIX}_LIBRARY )
				set( ${VAR_PREFIX}_FOUND TRUE PARENT_SCOPE )
				message( STATUS "  ${VAR_PREFIX}: Found (${${VAR_PREFIX}_LIBRARY})" )

			else()
				set( ${VAR_PREFIX}_FOUND FALSE PARENT_SCOPE )
				message( STATUS "  ${VAR_PREFIX}: Not found" )
			endif()

		else()
			pkg_check_modules( ${VAR_PREFIX} ${PKG_NAME} )
			if( ${VAR_PREFIX}_FOUND )
				message( STATUS "  ${VAR_PREFIX}: Found" )
				set( ${VAR_PREFIX}_FOUND TRUE PARENT_SCOPE )

			else()
				message( STATUS "  ${VAR_PREFIX}: Not found" )
				set( ${VAR_PREFIX}_FOUND FALSE PARENT_SCOPE )
			endif()
		endif()

	elseif( ${OPTION_NAME} )
		# Explicitly enabled
		if( APPLE )
			find_library( ${VAR_PREFIX}_LIBRARY NAMES ${LIBRARY_NAMES} REQUIRED )

		else()
			pkg_check_modules( ${VAR_PREFIX} REQUIRED ${PKG_NAME} )
		endif()
		set( ${VAR_PREFIX}_FOUND TRUE PARENT_SCOPE )
	endif()
endfunction()

# Detect libraries
detect_library( PNG libpng "png16;png" )
detect_library( JPEG libjpeg "jpeg" )
detect_library( WEBP libwebp "webp" )

# Build with detected features
add_executable( myapp src/main.c )

if( PNG_FOUND )
	target_compile_definitions( myapp PRIVATE HAVE_PNG )
	target_link_libraries( myapp PRIVATE ${PNG_LIBRARY} )
endif()

if( JPEG_FOUND )
	target_compile_definitions( myapp PRIVATE HAVE_JPEG )
	target_link_libraries( myapp PRIVATE ${JPEG_LIBRARY} )
endif()

if( WEBP_FOUND )
	target_compile_definitions( myapp PRIVATE HAVE_WEBP )
	target_link_libraries( myapp PRIVATE ${WEBP_LIBRARY} )
endif()

# Summary
message( STATUS "" )
message( STATUS "Feature Summary" )
message( STATUS "===============" )
message( STATUS "PNG:  ${PNG_FOUND}" )
message( STATUS "JPEG: ${JPEG_FOUND}" )
message( STATUS "WebP: ${WEBP_FOUND}" )
message( STATUS "" )
```
