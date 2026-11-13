import json
import os
import sys

from seesaw.item import Item


def main():
    os.chdir(os.path.dirname(sys.argv[1]))
    context = {
        'downloader': sys.argv[2]
    }
    with open(sys.argv[1]) as f:
        exec(f.read(), context)
    result = {
        'runnable': True
    }
    if 'warrior_check' in context:
        context['pipeline'].project = context['project']
        item = Item(
            pipeline=context['pipeline'],
            item_id='warrior-check',
            item_number=0,
            keep_data=True,
            prepare_data_directory=False
        )
        item.on_output += lambda item, data: sys.stderr.write(
            data.encode('ascii', 'replace').decode('ascii')
        )
        result = context['warrior_check'](item)
    print('\nwarrior_check_result: ' + json.dumps(result))


if __name__ == '__main__':
    main()
