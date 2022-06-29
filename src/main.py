import logging
import re
from collections import defaultdict
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import BASE_DIR, EXPECTED_STATUS, MAIN_DOC_URL, PEP_URL
from outputs import control_output
from utils import find_tag, get_response


def whats_new(session):
    """
    Собирает ссылки на статьи о нововведениях в Python,
    переходить по ним и забирает информацию об авторах и редакторах статей.
    """
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    if response is None:
        return

    soup = BeautifulSoup(response.text, features='lxml')

    main_div = find_tag(
        soup, 'section', attrs={'id': 'what-s-new-in-python'}
    )
    div_with_ul = find_tag(
        main_div, 'div', attrs={'class': 'toctree-wrapper'}
    )
    sections_by_python = div_with_ul.find_all(
        'li', attrs={'class': 'toctree-l1'}
    )

    results = [('Ссылка на статью', 'Заголовок', 'Редактор, Автор')]
    for section in tqdm(sections_by_python):
        version_a_tag = section.find('a')
        href = version_a_tag['href']
        version_link = urljoin(whats_new_url, href)

        response = get_response(session, version_link)
        if response is None:
            continue

        soup = BeautifulSoup(response.text, 'lxml')

        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        results.extend(
            (version_link, h1.text, dl.text)
        )

    return results


def latest_versions(session):
    """
    Собирает информацию о статусах версий Python.
    """
    response = get_response(session, MAIN_DOC_URL)
    if response is None:
        return

    soup = BeautifulSoup(response.text, 'lxml')

    sidebar = find_tag(soup, 'div', {'class': 'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    for ul in ul_tags:
        if 'All versions' in ul.text:
            a_tags = ul.find_all('a')
            break

    else:
        raise Exception('Не найден список c версиями Python')

    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'
    for a_tag in a_tags:
        link = a_tag['href']
        text_match = re.search(pattern, a_tag.text)
        if text_match is not None:
            version, status = text_match.groups()
        else:
            version, status = a_tag.text, ''
        results.append(
            (link, version, status)
        )

    return results


def download(session):
    """
    Скачивает zip архив с актуальной документацией в pdf формате.
    """
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    if response is None:
        return

    soup = BeautifulSoup(response.text, 'lxml')

    table_tag = find_tag(soup, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = table_tag.find('a', {'href': re.compile(r'.+pdf-a4\.zip$')})
    pdf_a4_link = pdf_a4_tag['href']
    archive_url = urljoin(downloads_url, pdf_a4_link)

    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename

    response = session.get(archive_url)

    with open(archive_path, 'wb') as file:
        file.write(response.content)

    logging.info(f'Архив был загружен и сохранён: {archive_path}')


def pep(session):
    """
    Парсер для вывода списка статусов документов PEP
    и количество документов в каждом статусе, а также
    для проверки соответствия статусов на главной
    и на персональной страницах документа.
    """
    response = get_response(session, PEP_URL)
    if response is None:
        return

    soup = BeautifulSoup(response.text, features='lxml')

    section_tag = find_tag(soup, 'section', attrs={'id': 'numerical-index'})
    tbody_tag = find_tag(section_tag, 'tbody')
    tr_tags = tbody_tag.find_all('tr')

    results = [('Cтатус', 'Количество')]
    pep_status_count = defaultdict(int)
    total_pep_count = 0
    for tr_tag in tqdm(tr_tags):
        td_tags_with_a_tags = find_tag(
            tr_tag, 'td').find_next_sibling('td')
        total_pep_count += 1

        for td_next_tag in td_tags_with_a_tags:
            link = td_next_tag['href']
            pep_url = urljoin(PEP_URL, link)

            response = get_response(session, pep_url)

            soup = BeautifulSoup(
                response.text, features='lxml'
            )

            dl_tag = find_tag(
                soup, 'dl', attrs={'class': 'rfc2822 field-list simple'}
            )
            dd_tag = find_tag(
                dl_tag, 'dt', attrs={'class': 'field-even'}
            ).find_next_sibling('dd')
            status_personal_page = dd_tag.string
            status_pep_general_table = find_tag(
                tr_tag, 'td').string[1:]
            try:
                if status_personal_page not in (
                        EXPECTED_STATUS[status_pep_general_table]):
                    if len(status_pep_general_table) > 2 or (
                            EXPECTED_STATUS[status_pep_general_table] is None):
                        raise KeyError('Получен неожиданный статус')
                    logging.info(
                        f'Несовпадающие статусы:\n {pep_url}\n'
                        f'Cтатус на персональной странице: '
                        f'{status_personal_page}\n'
                        f'Ожидаемые статусы: '
                        f'{EXPECTED_STATUS[status_pep_general_table]}'
                    )

            except KeyError:
                logging.warning('Получен некорректный статус')

            else:
                pep_status_count[
                    status_personal_page] = pep_status_count.get(
                    status_personal_page, 0) + 1

    results.extend(pep_status_count.items())
    results.append(('Total: ', total_pep_count))
    return results


MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep,
}


def main():
    """
    Конфигурация парсера аргументов командной строки и
    получение строки нужного режима работы с возможностью логирования.
    """
    configure_logging()
    logging.info('Парсер запущен!')

    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    logging.info(f'Аргументы командной строки: {args}')

    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()

    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)

    if results is not None:
        control_output(results, args)

    logging.info('Парсер завершил работу.')


if __name__ == '__main__':
    main()
