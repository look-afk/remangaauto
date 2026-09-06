## Python Playwright template

<!-- This is an Apify template readme -->

## Included features

- **[Apify SDK](https://docs.apify.com/sdk/python/)** for Python - a toolkit for building Apify [Actors](https://apify.com/actors) and scrapers in Python
- **[Input schema](https://docs.apify.com/platform/actors/development/input-schema)** - define and easily validate a schema for your Actor's input
- **[Request queue](https://docs.apify.com/sdk/python/docs/concepts/storages#working-with-request-queues)** - queues into which you can put the URLs you want to scrape
- **[Dataset](https://docs.apify.com/sdk/python/docs/concepts/storages#working-with-datasets)** - store structured data where each object stored has the same attributes
- **[Playwright](https://pypi.org/project/playwright/)** - a browser automation library

## Resources

- [Playwright for web scraping in 2023](https://blog.apify.com/how-to-scrape-the-web-with-playwright-ece1ced75f73/)
- [Scraping single-page applications with Playwright](https://blog.apify.com/scraping-single-page-applications-with-playwright/)
- [How to scale Puppeteer and Playwright](https://blog.apify.com/how-to-scale-puppeteer-and-playwright/)
- [Integration with Zapier](https://apify.com/integrations), Make, GitHub, Google Drive and other apps
- [Video guide on getting data using Apify API](https://www.youtube.com/watch?v=ViYYDHSBAKM)
- A short guide on how to build web scrapers using code templates:

[web scraper template](https://www.youtube.com/watch?v=u-i-Korzf8w)


## Getting started

For complete information [see this article](https://docs.apify.com/platform/actors/development#build-actor-at-apify-console). In short, you will:

1. Build the Actor
2. Run the Actor

## Pull the Actor for local development

If you would like to develop locally, you can pull the existing Actor from Apify console using Apify CLI:

1. Install `apify-cli`

    **Using Homebrew**

    ```bash
    brew install apify-cli
    ```

    **Using NPM**

    ```bash
    npm -g install apify-cli
    ```

2. Pull the Actor by its unique `<ActorId>`, which is one of the following:
    - unique name of the Actor to pull (e.g. "apify/hello-world")
    - or ID of the Actor to pull (e.g. "E2jjCZBezvAZnX8Rb")

    You can find both by clicking on the Actor title at the top of the page, which will open a modal containing both Actor unique name and Actor ID.

    This command will copy the Actor into the current directory on your local machine.

    ```bash
    apify pull <ActorId>
    ```

## Documentation reference

To learn more about Apify and Actors, take a look at the following resources:

- [Apify SDK for JavaScript documentation](https://docs.apify.com/sdk/js)
- [Apify SDK for Python documentation](https://docs.apify.com/sdk/python)
- [Apify Platform documentation](https://docs.apify.com/platform)
- [Join our developer community on Discord](https://discord.com/invite/jyEM2PRvMU)
