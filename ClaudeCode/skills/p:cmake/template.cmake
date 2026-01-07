# Modern CMake Template with Static Linking
# Copy this file as CMakeLists.txt and customize

cmake_minimum_required( VERSION 3.15 )
project( myproject VERSION 1.0.0 LANGUAGES C )

# ==============================================================================
# Build Configuration
# ==============================================================================

# Set default build type
if( NOT CMAKE_BUILD_TYPE )
	set( CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Choose the type of build" FORCE )
endif()

# Export compile_commands.json for IDE support
set( CMAKE_EXPORT_COMPILE_COMMANDS ON )

# C standard
set( CMAKE_C_STANDARD 11 )
set( CMAKE_C_STANDARD_REQUIRED ON )

# Build options
option( BUILD_SHARED_LIBS "Build shared libraries" OFF )
option( BUILD_TESTING "Build tests" ON )
option( ENABLE_FEATURE "Enable optional feature" ON )

# ==============================================================================
# Platform-Specific Static Linking
# ==============================================================================

# Force static library builds
set( BUILD_SHARED_LIBS OFF CACHE BOOL "Build shared libraries" FORCE )

# Platform-specific static linking
# macOS doesn't support fully static binaries ( libSystem must be dynamic )
# Only link third-party libraries statically on macOS
if( UNIX AND NOT APPLE )
	# Linux: Full static linking
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static" )
	message( STATUS "Platform: Linux ( full static linking )" )

elseif( APPLE )
	# macOS: Static third-party libs, dynamic system libs
	# Only search for .a files to force static linking
	set( CMAKE_FIND_LIBRARY_SUFFIXES .a )
	message( STATUS "Platform: macOS ( hybrid static linking )" )

elseif( WIN32 )
	# Windows: Static linking with /MT flag
	set( CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" )
	message( STATUS "Platform: Windows ( static runtime )" )
endif()

# ==============================================================================
# Compiler Flags
# ==============================================================================

# Common flags for GCC/Clang
if( CMAKE_C_COMPILER_ID MATCHES "GNU|Clang" )
	add_compile_options(
		-Wall                    # All warnings
		-Wextra                  # Extra warnings
		-Werror                  # Warnings as errors
		-O3                      # Max optimization ( Release )
		-g                       # Debug symbols
	)
endif()

# MSVC flags
if( MSVC )
	add_compile_options(
		/W4                      # Warning level 4
		/WX                      # Warnings as errors
		/O2                      # Optimize for speed
	)
endif()

# Platform-specific linker flags
if( UNIX AND NOT APPLE )
	# Linux: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-z,stack-size=8388608" )

elseif( APPLE )
	# macOS: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-stack_size,0x800000" )

elseif( WIN32 )
	# Windows: Set stack size
	set( CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} /STACK:8388608" )
endif()

# ==============================================================================
# Dependencies
# ==============================================================================

find_package( PkgConfig REQUIRED )

# ------------------------------------------------------------------------------
# zlib ( REQUIRED )
# ------------------------------------------------------------------------------
if( APPLE )
	# macOS: Find static library explicitly
	find_library( ZLIB_LIBRARY NAMES z REQUIRED
		HINTS
			/usr/local/opt/zlib/lib
			/usr/local/lib
			/opt/homebrew/opt/zlib/lib
			/opt/homebrew/lib
	)
	find_path( ZLIB_INCLUDE_DIR zlib.h
		HINTS
			/usr/local/opt/zlib/include
			/usr/local/include
			/opt/homebrew/opt/zlib/include
			/opt/homebrew/include
	)
	if( ZLIB_LIBRARY AND ZLIB_INCLUDE_DIR )
		set( ZLIB_LIBRARIES ${ZLIB_LIBRARY} )
		set( ZLIB_INCLUDE_DIRS ${ZLIB_INCLUDE_DIR} )
		set( ZLIB_FOUND TRUE )
	endif()

else()
	# Linux/Windows: Use pkg-config
	pkg_check_modules( ZLIB REQUIRED zlib )
endif()

# ------------------------------------------------------------------------------
# libpng ( OPTIONAL EXAMPLE )
# ------------------------------------------------------------------------------
if( ENABLE_FEATURE )
	if( APPLE )
		find_library( PNG_LIBRARY NAMES png16 png
			HINTS
				/usr/local/opt/libpng/lib
				/opt/homebrew/opt/libpng/lib
		)
		find_path( PNG_INCLUDE_DIR png.h PATH_SUFFIXES libpng16 libpng
			HINTS
				/usr/local/opt/libpng/include
				/opt/homebrew/opt/libpng/include
		)
		if( PNG_LIBRARY AND PNG_INCLUDE_DIR )
			set( PNG_LIBRARIES ${PNG_LIBRARY} )
			set( PNG_INCLUDE_DIRS ${PNG_INCLUDE_DIR} )
			set( PNG_FOUND TRUE )
		endif()

	else()
		pkg_check_modules( PNG libpng16 )
		if( NOT PNG_FOUND )
			pkg_check_modules( PNG libpng )
		endif()
	endif()

	if( PNG_FOUND )
		message( STATUS "PNG support: ENABLED" )

	else()
		message( WARNING "libpng not found, PNG support disabled" )
		set( ENABLE_FEATURE OFF )
	endif()
endif()

# ==============================================================================
# Source Files
# ==============================================================================

set( PROJECT_SOURCES
	src/main.c
	src/core.c
	src/utils.c
 )

# Platform-specific sources
if( WIN32 )
	list( APPEND PROJECT_SOURCES src/platform_windows.c )
else()
	list( APPEND PROJECT_SOURCES src/platform_unix.c )
endif()

# ==============================================================================
# Targets
# ==============================================================================

# Main executable
add_executable( ${PROJECT_NAME} ${PROJECT_SOURCES} )

# Include directories ( modern target-based )
target_include_directories( ${PROJECT_NAME} PRIVATE
	${CMAKE_SOURCE_DIR}/include
	${ZLIB_INCLUDE_DIRS}
 )

# Link libraries ( modern target-based )
target_link_libraries( ${PROJECT_NAME} PRIVATE
	${ZLIB_LIBRARIES}
 )

# Optional library
if( PNG_FOUND )
	target_compile_definitions( ${PROJECT_NAME} PRIVATE HAVE_PNG )
	target_include_directories( ${PROJECT_NAME} PRIVATE ${PNG_INCLUDE_DIRS} )
	target_link_libraries( ${PROJECT_NAME} PRIVATE ${PNG_LIBRARIES} )
endif()

# Platform-specific libraries
if( UNIX AND NOT APPLE )
	# Linux: Link math library
	target_link_libraries( ${PROJECT_NAME} PRIVATE m pthread )
endif()

# ==============================================================================
# Testing
# ==============================================================================

if( BUILD_TESTING )
	enable_testing()

	# Create library for tests ( without main.c )
	set( LIB_SOURCES ${PROJECT_SOURCES} )
	list( REMOVE_ITEM LIB_SOURCES src/main.c )

	add_library( ${PROJECT_NAME}_lib STATIC ${LIB_SOURCES} )

	target_include_directories( ${PROJECT_NAME}_lib PUBLIC
		${CMAKE_SOURCE_DIR}/include
		${ZLIB_INCLUDE_DIRS}
	 )

	target_link_libraries( ${PROJECT_NAME}_lib PUBLIC
		${ZLIB_LIBRARIES}
	 )

	if( PNG_FOUND )
		target_compile_definitions( ${PROJECT_NAME}_lib PUBLIC HAVE_PNG )
		target_include_directories( ${PROJECT_NAME}_lib PUBLIC ${PNG_INCLUDE_DIRS} )
		target_link_libraries( ${PROJECT_NAME}_lib PUBLIC ${PNG_LIBRARIES} )
	endif()

	# Test executable
	add_executable( ${PROJECT_NAME}_test
		tests/test_core.c
		tests/test_utils.c
	 )

	target_link_libraries( ${PROJECT_NAME}_test PRIVATE ${PROJECT_NAME}_lib )

	# Register test
	add_test( NAME ${PROJECT_NAME}_test COMMAND ${PROJECT_NAME}_test )
endif()

# ==============================================================================
# Installation
# ==============================================================================

install( TARGETS ${PROJECT_NAME} DESTINATION bin )

# ==============================================================================
# Configuration Summary
# ==============================================================================

message( STATUS "" )
message( STATUS "============================" )
message( STATUS "${PROJECT_NAME} Configuration Summary" )
message( STATUS "============================" )
message( STATUS "Version: ${PROJECT_VERSION}" )
message( STATUS "Build type: ${CMAKE_BUILD_TYPE}" )
message( STATUS "Platform: ${CMAKE_SYSTEM_NAME}" )
message( STATUS "Processor: ${CMAKE_SYSTEM_PROCESSOR}" )
message( STATUS "C compiler: ${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}" )
message( STATUS "" )
message( STATUS "Core Dependencies:" )
message( STATUS "  zlib: ${ZLIB_LIBRARIES}" )
message( STATUS "" )
message( STATUS "Optional Features:" )
message( STATUS "  PNG support: ${ENABLE_FEATURE}" )
if( PNG_FOUND )
	message( STATUS "    libpng: ${PNG_LIBRARIES}" )
endif()
message( STATUS "" )
message( STATUS "Build Options:" )
message( STATUS "  Static linking: ON" )
message( STATUS "  Testing: ${BUILD_TESTING}" )
message( STATUS "" )
message( STATUS "Installation:" )
message( STATUS "  Binary: ${CMAKE_INSTALL_PREFIX}/bin" )
message( STATUS "" )
message( STATUS "============================" )
message( STATUS "" )

# ==============================================================================
# Post-build verification hint
# ==============================================================================

if( UNIX AND NOT APPLE )
	message( STATUS "After build, verify static linking with: ldd ./${PROJECT_NAME}" )

elseif( APPLE )
	message( STATUS "After build, verify static linking with: otool -L ./${PROJECT_NAME}" )
	message( STATUS "Expected: Only /usr/lib/libSystem.B.dylib should be listed" )
endif()

