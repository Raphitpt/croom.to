"""
Tests for croom.installer.packaging module.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestArchitecture:
    """Tests for Architecture enum."""

    def test_values(self):
        """Test architecture enum values."""
        from croom.installer.packaging import Architecture

        assert Architecture.ARM64.value == "arm64"
        assert Architecture.ARMHF.value == "armhf"
        assert Architecture.AMD64.value == "amd64"


class TestDistribution:
    """Tests for Distribution enum."""

    def test_raspberry_pi_os_values(self):
        """Test Raspberry Pi OS distribution values."""
        from croom.installer.packaging import Distribution

        assert Distribution.BOOKWORM.value == "bookworm"
        assert Distribution.BULLSEYE.value == "bullseye"

    def test_ubuntu_values(self):
        """Test Ubuntu distribution values."""
        from croom.installer.packaging import Distribution

        assert Distribution.JAMMY.value == "jammy"
        assert Distribution.NOBLE.value == "noble"


class TestPackageType:
    """Tests for PackageType enum."""

    def test_values(self):
        """Test package type enum values."""
        from croom.installer.packaging import PackageType

        assert PackageType.CORE.value == "croom-core"
        assert PackageType.UI.value == "croom-ui"
        assert PackageType.AI.value == "croom-ai"
        assert PackageType.FULL.value == "croom"


class TestPackageInfo:
    """Tests for PackageInfo dataclass."""

    def test_basic_package(self):
        """Test basic package info."""
        from croom.installer.packaging import PackageInfo, Architecture, Distribution

        info = PackageInfo(
            name="croom-core",
            version="2.0.0",
            architecture=Architecture.ARM64,
            distribution=Distribution.BOOKWORM,
            description="Croom Core",
        )
        assert info.name == "croom-core"
        assert info.version == "2.0.0"
        assert info.architecture == Architecture.ARM64
        assert info.depends == []

    def test_control_content(self):
        """Test debian/control generation."""
        from croom.installer.packaging import PackageInfo, Architecture, Distribution

        info = PackageInfo(
            name="croom-core",
            version="2.0.0",
            architecture=Architecture.ARM64,
            distribution=Distribution.BOOKWORM,
            description="Croom Core",
            depends=["python3", "chromium"],
        )
        control = info.get_control_content()

        assert "Package: croom-core" in control
        assert "Architecture: arm64" in control
        assert "Depends: python3, chromium" in control
        assert "Recommends:" not in control


class TestPackageDefinitions:
    """Tests for package definitions."""

    def test_raspberry_pi_definitions_exist(self):
        """Test Raspberry Pi (Debian) package definitions exist."""
        from croom.installer.packaging import PACKAGE_DEFINITIONS, PackageType

        assert PackageType.CORE in PACKAGE_DEFINITIONS
        assert PackageType.UI in PACKAGE_DEFINITIONS

    def test_amd64_definitions_exist(self):
        """Test AMD64 package definitions exist."""
        from croom.installer.packaging import PACKAGE_DEFINITIONS_AMD64, PackageType

        assert PackageType.CORE in PACKAGE_DEFINITIONS_AMD64

    def test_get_package_definitions_arm64(self):
        """Test ARM64 on Debian gets the Raspberry Pi definitions."""
        from croom.installer.packaging import (
            PACKAGE_DEFINITIONS, Architecture, Distribution, get_package_definitions,
        )

        defs = get_package_definitions(Architecture.ARM64, Distribution.BOOKWORM)
        assert defs is PACKAGE_DEFINITIONS

    def test_get_package_definitions_amd64_debian(self):
        """Test AMD64 on Debian gets the AMD64 definitions."""
        from croom.installer.packaging import (
            PACKAGE_DEFINITIONS_AMD64, Architecture, Distribution, get_package_definitions,
        )

        defs = get_package_definitions(Architecture.AMD64, Distribution.BOOKWORM)
        assert defs is PACKAGE_DEFINITIONS_AMD64

    def test_get_package_definitions_amd64_ubuntu(self):
        """Test AMD64 on Ubuntu gets the Ubuntu definitions."""
        from croom.installer.packaging import (
            PACKAGE_DEFINITIONS_UBUNTU, Architecture, Distribution, get_package_definitions,
        )

        for dist in (Distribution.JAMMY, Distribution.NOBLE):
            assert get_package_definitions(Architecture.AMD64, dist) is PACKAGE_DEFINITIONS_UBUNTU
