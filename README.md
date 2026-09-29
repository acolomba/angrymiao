# angrymiao

Command-line firmware upgrades for Angry Miao devices

## Installation

```bash
pip install angrymiao
```

## Usage

```bash
angrymiao
```

## Development

```bash
# clone and setup
git clone https://github.com/acolomba/angrymiao.git
cd angrymiao

# create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# install pre-commit hooks
pre-commit install
pre-commit install --hook-type commit-msg
```

## Testing

```bash
# unit tests
pytest test/ -v

# integration tests
behave
```

## License

This project is licensed under the MIT license. See the [LICENSE](LICENSE) file for details.
