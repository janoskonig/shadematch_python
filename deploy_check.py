#!/usr/bin/env python3
"""
Deployment check script for ShadeMatch Python
This script helps verify the environment and dependencies before deployment.
"""

import sys
import importlib
import os
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible."""
    version = sys.version_info
    print(f"Python version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major == 3 and version.minor >= 8:
        print("✅ Python version is compatible")
        return True
    else:
        print("❌ Python version should be 3.8 or higher")
        return False

def check_dependencies():
    """Check if all required dependencies can be imported."""
    required_packages = [
        'flask',
        'flask_sqlalchemy',
        'sqlalchemy',
        'gunicorn',
        'python-dotenv',
        'colormath',
        'numpy',
        'pandas',
        'matplotlib',
        'openpyxl',
        'plotly',
        'psycopg2',
        'PIL',
        'scipy',
        'statsmodels',
        'sklearn'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            if package == 'PIL':
                importlib.import_module('PIL')
            elif package == 'sklearn':
                importlib.import_module('sklearn')
            else:
                importlib.import_module(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package}")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\nMissing packages: {', '.join(missing_packages)}")
        return False
    else:
        print("\n✅ All required packages are available")
        return True

def check_environment():
    """Check environment variables and configuration."""
    print("\n=== Environment Check ===")
    
    # Check for .env file
    env_file = Path('.env')
    if env_file.exists():
        print("✅ .env file found")
    else:
        print("⚠️  .env file not found (create one for local development)")
    
    # Check for required environment variables
    required_vars = ['DATABASE_URL', 'SECRET_KEY']
    for var in required_vars:
        value = os.getenv(var)
        if value:
            if var == 'DATABASE_URL':
                # Mask the password for security
                masked = value.replace('://', '://***:***@') if '@' in value else value
                print(f"✅ {var}: {masked}")
            else:
                print(f"✅ {var}: {'*' * len(value)}")
        else:
            print(f"❌ {var}: Not set")
    
    return True

def check_files():
    """Check if all required files exist."""
    print("\n=== File Check ===")
    
    required_files = [
        'requirements.txt',
        'run.py',
        'config.py',
        'app/__init__.py',
        'app/models.py',
        'app/routes.py'
    ]
    
    missing_files = []
    
    for file_path in required_files:
        if Path(file_path).exists():
            print(f"✅ {file_path}")
        else:
            print(f"❌ {file_path}")
            missing_files.append(file_path)
    
    if missing_files:
        print(f"\nMissing files: {', '.join(missing_files)}")
        return False
    else:
        print("\n✅ All required files are present")
        return True

def main():
    """Run all checks."""
    print("ShadeMatch Python - Deployment Check")
    print("=" * 40)
    
    checks = [
        check_python_version(),
        check_dependencies(),
        check_environment(),
        check_files()
    ]
    
    print("\n" + "=" * 40)
    if all(checks):
        print("🎉 All checks passed! Your environment is ready for deployment.")
        return 0
    else:
        print("⚠️  Some checks failed. Please resolve the issues before deployment.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
