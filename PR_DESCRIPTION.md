# Python 3.11 Upgrade for explainshell

## Overview
This PR upgrades explainshell from Python 2.7 to Python 3.11, modernizing the codebase and ensuring compatibility with current Python versions.

## 🚀 Key Changes

### Python Version Upgrade
- **Dockerfile**: Updated base image from `python:2.7` to `python:3.11`
- **CI/CD**: Updated GitHub Actions workflow to use Python 3.11
- **Dependencies**: All requirements updated to Python 3.11 compatible versions

### Testing Framework Migration
- **From**: `nose` (Python 2 era testing framework)
- **To**: `pytest` (modern Python testing framework)
- **Makefile**: Updated test commands to use `pytest -q`
- **Test Files**: Updated all test assertions (`assertEquals` → `assertEqual`)

### Code Modernization
- **Imports**: Updated `urllib` imports to `urllib.parse`
- **Print Statements**: Converted to Python 3 function calls
- **Exception Handling**: Updated syntax (`except Exception, e:` → `except Exception as e:`)
- **Dictionary Methods**: Replaced `.iteritems()` with `.items()`
- **Integer Division**: Fixed division operations for Python 3 behavior
- **Type Hints**: Added modern Python type annotations

### Database Compatibility
- **PyMongo API**: Updated deprecated methods:
  - `cursor.count()` → `cursor.count_documents({})`
  - `collection.insert()` → `collection.insert_one()`
  - `collection.remove()` → `collection.delete_many()`
  - `collection.update()` → `collection.update_one()`

### Collections Compatibility
- **Python 3.10+ Support**: Added compatibility shims for removed collections aliases:
  - `collections.MutableSet` → `collections.abc.MutableSet`
  - `collections.Mapping` → `collections.abc.Mapping`
  - `collections.MutableMapping` → `collections.abc.MutableMapping`

## 📁 Files Modified

### Configuration Files
- `Dockerfile` - Python 3.11 base image
- `requirements.txt` - Updated dependencies
- `Makefile` - pytest integration
- `.github/workflows/build-test.yml` - CI updates

### Core Application
- `explainshell/matcher.py` - Command parsing and matching
- `explainshell/store.py` - MongoDB operations
- `explainshell/manpage.py` - Man page processing
- `explainshell/util.py` - Utility functions
- `explainshell/manager.py` - Application management
- `explainshell/options.py` - Option handling

### Web Components
- `explainshell/web/views.py` - Web routes and views
- `explainshell/web/helpers.py` - Helper functions
- `explainshell/web/debugviews.py` - Debug views
- `explainshell/web/__init__.py` - Web app initialization

### Algorithm Modules
- `explainshell/algo/classifier.py` - Text classification
- `explainshell/algo/features.py` - Feature extraction

### Test Files
- All test files in `tests/` directory updated for pytest compatibility

## 🧪 Testing

### Test Results
- ✅ All existing tests pass with pytest
- ✅ Web application starts successfully
- ✅ Docker container runs without errors
- ✅ Command parsing and explanation functionality works

### Test Commands
```bash
# Run all tests
pytest -q

# Run specific test files
pytest tests/test-matcher.py
pytest tests/test-manager.py

# Run with coverage
pytest --cov=explainshell
```

## 🐳 Docker Support

### Local Development
```bash
# Build and run with Docker Compose
docker compose up -d

# Access the application
open http://localhost:5000
```

### Production Build
```bash
# Build production image
docker build -t explainshell:latest .

# Run container
docker run -p 5000:5000 explainshell:latest
```

## 🔧 Breaking Changes

### For Users
- **None**: This is a backward-compatible upgrade
- All existing functionality preserved
- API remains the same

### For Developers
- **Python Version**: Requires Python 3.11+
- **Testing**: Use `pytest` instead of `nose`
- **Dependencies**: Updated package versions

## 📋 Checklist

- [x] Python 3.11 compatibility verified
- [x] All tests passing
- [x] Docker container working
- [x] Web application functional
- [x] Code style and linting clean
- [x] Documentation updated
- [x] Dependencies updated and tested

## 🎯 Benefits

1. **Security**: Python 3.11 includes latest security patches
2. **Performance**: Python 3.11 is significantly faster than Python 2.7
3. **Maintainability**: Modern Python features and syntax
4. **Ecosystem**: Access to current Python packages and tools
5. **Future-proof**: Long-term support and compatibility

## 🔗 Related Issues

- Addresses Python 2.7 end-of-life concerns
- Enables use of modern Python packages
- Improves development experience with current tools

---

**Note**: This upgrade maintains full backward compatibility while modernizing the codebase for future development.
