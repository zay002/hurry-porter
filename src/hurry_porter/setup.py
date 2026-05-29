from setuptools import find_packages, setup

package_name = "hurry_porter"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/hurry.example.toml"]),
    ],
    install_requires=["setuptools"],
    python_requires=">=3.12,<3.13",
    zip_safe=True,
    maintainer="zay002",
    maintainer_email="zay002@example.com",
    description="Hardware orchestration CLI for ROS 2 on WSL2.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "hurry = hurry_porter.cli:main",
        ],
    },
)

