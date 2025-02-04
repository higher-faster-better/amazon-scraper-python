import requests
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time

# Constants
_BASE_URL = "https://www.amazon.com/"
_DEFAULT_BEAUTIFULSOUP_PARSER = "html.parser"
_USER_AGENT_LIST = [
    'Mozilla/5.0 (Linux; Android 7.0; SM-A520F Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/65.0.3325.109 Mobile Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.79 Safari/537.36'
]

CSS_SELECTORS = {
    'mobile': {
        "product": "#resultItems > li",
        "title": "a > div > div.sx-table-detail > h5 > span",
        "rating": "a > div > div.sx-table-detail > div.a-icon-row.a-size-small > i > span",
        "review_nb": "a > div > div.sx-table-detail > div.a-icon-row.a-size-small > span",
        "url": "a[href]",
        "img": "img[src]",
        "next_page_url": "ul.a-pagination > li.a-last > a[href]"
    },
    'mobile_grid': {
        "product": "#grid-atf-content > li > div.s-item-container",
        "title": "a > div > h5.sx-title > span",
        "rating": "a > div > div.a-icon-row.a-size-mini > i > span",
        "review_nb": "a > div > div.a-icon-row.a-size-mini > span",
        "url": "a[href]",
        "img": "img[src]",
        "next_page_url": "ul.a-pagination > li.a-last > a[href]"
    },
    'desktop': {
        "product": "ul > li.s-result-item > div.s-item-container",
        "title": "a.s-access-detail-page > h2",
        "rating": "i.a-icon-star > span",
        "review_nb": "div.a-column.a-span5.a-span-last > div.a-row.a-spacing-mini > a.a-size-small.a-link-normal.a-text-normal",
        "url": "div.a-row.a-spacing-small > div.a-row.a-spacing-none > a[href]",
        "img": "div.a-column.a-span12.a-text-center > a.a-link-normal.a-text-normal > img[src]",
        "next_page_url": "a#pagnNextLink"
    },
    'desktop_2': {
        "product": "div.s-result-list.sg-row > div.s-result-item",
        "title": "div div.sg-row  h5 > span",
        "rating": "div div.sg-row .a-spacing-top-mini i span",
        "review_nb": "div div.sg-row .a-spacing-top-mini span.a-size-small",
        "url": "div div a.a-link-normal",
        "img": "img[src]",
        "next_page_url": "li.a-last > a[href]"
    }
}

# Max retry settings
_MAX_TRIAL_REQUESTS = 5
_WAIT_TIME_BETWEEN_REQUESTS = 1


class AmazonClient:
    def __init__(self):
        self.session = requests.session()
        self.current_user_agent_index = 0
        self.headers = {
            'Host': 'www.amazon.com',
            'User-Agent': _USER_AGENT_LIST[0],
            'Accept': 'text/html,application/xhtml+xml, application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
        }
        self.product_dict_list = []
        self.html_pages = []

    def _change_user_agent(self):
        """Switch User-Agent for the requests."""
        self.current_user_agent_index = (self.current_user_agent_index + 1) % len(_USER_AGENT_LIST)
        self.headers['User-Agent'] = _USER_AGENT_LIST[self.current_user_agent_index]

    def _get(self, url):
        """Send a GET request with appropriate headers."""
        response = self.session.get(url, headers=self.headers)
        if response.status_code != 200:
            raise ConnectionError(f'Status code {response.status_code} for url {url}')
        return response

    def _update_headers(self, search_url):
        """Update the 'Host' field in the header based on the Amazon domain."""
        domain = search_url.split("://")[1].split("/")[0]
        self.base_url = f"https://{domain}/"
        self.headers['Host'] = domain

    def _get_search_url(self, keywords):
        """Generate the search URL from the keywords."""
        return urljoin(_BASE_URL, f"s?k={keywords}")

    def _check_page(self, html_content):
        """Check if the page is valid for scraping."""
        invalid_keywords = ["Sign in for the best experience", "The request could not be satisfied.", "Robot Check"]
        return not any(keyword in html_content for keyword in invalid_keywords)

    def _get_page_html(self, search_url):
        """Retrieve the HTML page and handle retries if necessary."""
        trials = 0
        while trials < _MAX_TRIAL_REQUESTS:
            trials += 1
            try:
                res = self._get(search_url)
                if self._check_page(res.text):
                    return res.text
            except (requests.exceptions.SSLError, ConnectionError):
                pass
            self._change_user_agent()
            time.sleep(_WAIT_TIME_BETWEEN_REQUESTS)
        raise ValueError('No valid pages found!')

    def _get_n_ratings(self, product):
        """Extract the number of ratings from the product."""
        n_ratings_selectors = [
            "div.a-row.a-size-small span.a-size-base",
            "div div.sg-row .a-spacing-top-mini span.a-size-small",
            "div.a-column.a-span5.a-span-last > div.a-row.a-spacing-mini > a.a-size-small.a-link-normal.a-text-normal"
        ]
        for selector in n_ratings_selectors:
            n_ratings = _css_select(product, selector)
            try:
                return int(n_ratings.replace(',', ''))
            except ValueError:
                continue
        return float('nan')

    def _get_title(self, product):
        """Extract the product title."""
        title_selectors = ['h5 span', "a.s-access-detail-page > h2", "div div.sg-row h5 > span"]
        for selector in title_selectors:
            title = _css_select(product, selector)
            if title:
                return title
        return 'Title not found'

    def _get_rating(self, product):
        """Extract the product rating."""
        rating = re.search(r'(\d.\d) out of 5', str(product))
        return float(rating.group(1).replace(",", ".")) if rating else float('nan')

    def _get_prices(self, product):
        """Extract all prices of a product."""
        raw_prices = product.find_all(text=re.compile('\$[\d,]+.\d\d'))
        prices = {'prices_per_unit': set(), 'units': set(), 'prices_main': set()}
        for raw_price in raw_prices:
            price = float(re.search('\$([\d,]+.\d\d)', raw_price).group(1))
            if raw_price.parent.parent.attrs.get('data-a-strike') == 'true' or raw_price == '$0.00':
                continue
            elif raw_price.startswith('(') and '/' in raw_price:
                price_per_unit = re.findall(r'/(.*)\)', raw_price)[0]
                prices['prices_per_unit'].add(price)
                prices['units'].add(price_per_unit)
            else:
                prices['prices_main'].add(price)
        return {key: (value.pop() if len(value) == 1 else ', '.join(map(str, value))) if value else float('nan')
                for key, value in prices.items()}

    def _extract_page(self, page, max_product_nb):
        """Extract products from a page."""
        soup = BeautifulSoup(page, _DEFAULT_BEAUTIFULSOUP_PARSER)
        products = []
        for selector in CSS_SELECTORS.values():
            products = soup.select(selector.get('product', ''))
            if products:
                break
        for product in products:
            if len(self.product_dict_list) >= max_product_nb:
                break
            product_dict = {
                'title': self._get_title(product),
                'rating': self._get_rating(product),
                'review_nb': self._get_n_ratings(product),
                'img': self._get_img(product),
                'url': self._get_url(product),
                'asin': self._get_asin(product),
            }
            product_dict.update(self._get_prices(product))
            self.product_dict_list.append(product_dict)

        return self._get_next_page_url(soup)

    def _get_next_page_url(self, soup):
        """Extract the next page URL."""
        next_page_url = soup.select(CSS_SELECTORS['mobile']['next_page_url'])
        return urljoin(self.base_url, next_page_url[0].get('href')) if next_page_url else None

    def _get_img(self, product):
        """Extract the image URL."""
        img_url = _css_select(product, 'img[src]')
        return _get_high_res_img_url(img_url) if img_url else ''

    def _get_url(self, product):
        """Extract the product URL."""
        url = _css_select(product, 'a[href]')
        return urljoin(self.base_url, url) if url else ''

    def _get_asin(self, product):
        """Extract the ASIN from the product URL."""
        return self._get_url(product).split('/')[-1]

    def _get_products(self, keywords="", search_url="", max_product_nb=100):
        """Get products from Amazon based on search keywords."""
        if not search_url:
            search_url = self._get_search_url(keywords)
        self._update_headers(search_url)
        while len(self.product_dict_list) < max_product_nb:
            page = self._get_page_html(search_url)
            self.html_pages.append(page)
            search_url = self._extract_page(page, max_product_nb)
        return self.product_dict_list


def _css_select(soup, selector):
    """Select content by CSS selector."""
    selection = soup.select(selector)
    return selection[0].text.strip() if selection else None


def _get_high_res_img_url(url):
    """Get high-resolution image URL."""
    return re.sub(r'\._AC_.*?\.jpg', r'.jpg', url)


# Sample usage
if __name__ == "__main__":
    amazon_client = AmazonClient()
    products = amazon_client._get_products(keywords='laptop', max_product_nb=50)
    for product in products:
        print(product)
