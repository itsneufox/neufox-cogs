# Wiki Cog for Red Bot

A Red bot cog that provides search functionality for the open.mp documentation wiki.

## Features

- Search the open.mp documentation using Algolia search
- Rich formatting of documentation content
- Interactive menu system for browsing results
- Caching system for improved performance

## Installation

### Method 1: Using Red Bot's Downloader (Recommended)

```
[p]repo add omp-wiki-cog https://github.com/itsneufox/omp-wiki-cog
[p]cog install omp-wiki-cog wiki_cog
[p]load wiki_cog
```

### Method 2: Manual Installation

1. Download this repository
2. Copy the `wiki_cog` folder to your Red bot's `cogs` directory
3. Install dependencies: `pip install -r requirements.txt`
4. Load the cog: `[p]load wiki_cog`

### Method 3: Using pip

```bash
pip install wiki-cog
```

Then load: `[p]load wiki_cog`

## Configuration

No configuration needed! The cog works out of the box.

You can check the current configuration with:
```
[p]wikisetup
```

## Usage

### Basic Commands

- `[p]wiki <search term>` - Search the open.mp documentation
- `[p]wikisetup` - Setup command (owner only)

### Examples

```
[p]wiki GetPlayerName
[p]wiki OnPlayerConnect
[p]wiki SetPlayerPos
```

## Features

### Search Results

- Displays up to 5 relevant results
- Shows result type (Function, Callback, Native, etc.)
- Provides descriptions and links
- Interactive menu for detailed viewing

### Content Parsing

The cog intelligently parses documentation pages to extract:
- Function/callback descriptions
- Parameters with descriptions
- Code examples
- Tips and warnings
- Related functions/callbacks

### Caching

- Search results are cached for 10 minutes
- Improves response times for repeated searches
- Automatic cache cleanup

## Technical Details

### Dependencies

- `aiohttp` - For HTTP requests to Algolia API
- `beautifulsoup4` - For HTML parsing
- `lxml` - For XML/HTML parsing
- `redbot` - Red bot framework

### API Configuration

The cog uses Algolia search API with the following configuration:
- App ID: `AOKXGK39Z7`
- Index: `open`
- API Key: `54204f37e5c8fc2871052d595ee0505e`

## Troubleshooting

### Common Issues

1. **Cog won't load**: Check that all dependencies are installed
2. **Search returns no results**: Verify internet connection and API access
3. **Search not working**: Check your internet connection and try again

### Debug Commands

- `[p]reload wiki_cog` - Reload the cog
- `[p]unload wiki_cog` - Unload the cog

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Support

For support and questions:
- Create an issue on GitHub
- Join the Red bot support server
- Check the Red bot documentation