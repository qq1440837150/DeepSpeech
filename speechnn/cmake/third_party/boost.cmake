include(ExternalProject)

set(BOOST_PROJECT       "extern_boost")
# To release PaddlePaddle as a pip package, we have to follow the
# manylinux1 standard, which features as old Linux kernels and
# compilers as possible and recommends CentOS 5. Indeed, the earliest
# CentOS version that works with NVIDIA CUDA is CentOS 6.  And a new
# version of boost, say, 1.66.0, doesn't build on CentOS 6.  We
# checked that the devtools package of CentOS 6 installs boost 1.41.0.
# So we use 1.41.0 here.
set(BOOST_VER   "1.41.0")
set(BOOST_TAR   "boost_1_41_0" CACHE STRING "" FORCE)
set(BOOST_URL   "http://paddlepaddledeps.bj.bcebos.com/${BOOST_TAR}.tar.gz" CACHE STRING "" FORCE)

MESSAGE(STATUS "BOOST_VERSION: ${BOOST_VER}, BOOST_URL: ${BOOST_URL}")

set(BOOST_PREFIX_DIR ${THIRD_PARTY_PATH}/boost)
set(BOOST_SOURCE_DIR ${THIRD_PARTY_PATH}/boost/src/extern_boost)
cache_third_party(${BOOST_PROJECT}
        URL       ${BOOST_URL}
        DIR       BOOST_SOURCE_DIR)

set(BOOST_INCLUDE_DIR "${BOOST_SOURCE_DIR}" CACHE PATH "boost include directory." FORCE)
set_directory_properties(PROPERTIES CLEAN_NO_CUSTOM 1)
include_directories(${BOOST_INCLUDE_DIR})

if(WIN32 AND MSVC_VERSION GREATER_EQUAL 1600)
    add_definitions(-DBOOST_HAS_STATIC_ASSERT)
endif()

ExternalProject_Add(
    ${BOOST_PROJECT}
    ${EXTERNAL_PROJECT_LOG_ARGS}
    "${BOOST_DOWNLOAD_CMD}"
    URL_MD5               f891e8c2c9424f0565f0129ad9ab4aff
    PREFIX                ${BOOST_PREFIX_DIR}
    DOWNLOAD_DIR          ${BOOST_SOURCE_DIR}
    SOURCE_DIR            ${BOOST_SOURCE_DIR}
    DOWNLOAD_NO_PROGRESS  1
    CONFIGURE_COMMAND     ""
    BUILD_COMMAND         ""
    INSTALL_COMMAND       ""
    UPDATE_COMMAND        ""
    )

add_library(boost INTERFACE)

add_dependencies(boost ${BOOST_PROJECT})
set(Boost_INCLUDE_DIR ${BOOST_INCLUDE_DIR})
