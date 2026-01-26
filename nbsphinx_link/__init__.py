"""A sphinx extension for including notebook files from outside sphinx source root.

Usage:
- Install the package.
- Add 'nbsphinx_link' to extensions in Sphinx config 'conf.py'
- Add a file with the '.nblink' extension where you want them included.

The .nblink file is a JSON file with the following structure:

{
    "path": "relative/path/to/notebook"
}


Optionally the "extra-media" key can be added, if your notebook includes
any media, i.e. images. The value needs to be an array of strings,
which are paths to the media files or directories.

Further keys might be added in the future.
"""

import json
import os
import shutil
from pathlib import Path

import nbformat
from docutils import io
from docutils.nodes import document as ddocument
from nbsphinx import NotebookError, NotebookParser, _ipynbversion
from sphinx.util.logging import getLogger
from sphinx.application import Sphinx

from ._version import __version__


def register_dependency(file_path: Path, document: ddocument):
    """
    Registers files as dependency, so sphinx rebuilds the docs
    when they changed.

    Parameters
    ----------
    file_path : Path
        the Path to register for updates
    document: docutils.nodes.document
        Parsed document instance.
    """
    document.settings.record_dependencies.add(str(file_path))
    document.settings.env.note_dependency(file_path)


def copy_file(src: Path, dest: Path, document: ddocument):
    """
    Copies a singe file from ``src`` to ``dest``.

    Parameters
    ----------
    src : Path
        Path to the source file.
    dest : Path
        Path to the destination file or directory.
    document: docutils.nodes.document
        Parsed document instance.
    """
    logger = getLogger(__name__)
    try:
        shutil.copy(src, dest)
        register_dependency(src, document)
    except (OSError) as e:
        logger.warning("The the file %s couldn't be copied. Error:\n %s", src, e)


def copy_and_register_files(src: Path, dest: Path, document: ddocument):
    """
    Copies a directory or file from the path ``src`` to ``dest``
    and registers all files as dependency,
    so sphinx rebuilds the docs when they changed.

    Parameters
    ----------
    src : Path
        Path to the source directory or file
    dest : Path
        Path to the destination directory or file
    document: docutils.nodes.document
        Parsed document instance.
    """
    if src.is_dir():
        for root, _, filenames in os.walk(src):
            dst_root = Path(dest) / Path(root).relative_to(src)
            if filenames and not dst_root.exists():
                os.makedirs(dst_root)
            for filename in filenames:
                src_path = (Path(root) / filename).resolve()
                copy_file(src_path, dst_root, document)
    else:
        copy_file(src, dest, document)


def collect_extra_media(extra_media: list[str], source_file: Path, nb_path: Path, document: ddocument):
    """
    Collects extra media defined in the .nblink file,  with the key
    'extra-media'. The extra media (i.e. images) need to be copied
    in order for nbsphinx to properly render the notebooks, since
    nbsphinx assumes that the files are relative to the .nblink.

    Parameters
    ----------
    extra_media : list
        Paths to directories and/or files with extra media.
    source_file : str
        Path to the .nblink file.
    nb_path : str
        Path to the notebook defined in the .nblink file , with the key 'path'.
    document: docutils.nodes.document
        Parsed document instance.

    """
    any_dirs = False
    logger = getLogger(__name__)
    source_dir = source_file.parent
    if not isinstance(extra_media, list):
        logger.warning(
            'The "extra-media", defined in {} needs to be a list of paths. '
            'The current value is:\n{}'.format(source_file, extra_media)
        )
    for extract_media_path in extra_media:
        if Path(extract_media_path).is_absolute():
            src_path = Path(extract_media_path)
        else:
            extract_media_relpath = Path(source_dir) / extract_media_path
            src_path = (Path(source_dir) / extract_media_relpath).resolve()

        dest_path = src_path.relative_to(nb_path)
        dest_path = Path(source_dir) / dest_path

        if src_path.exists():
            any_dirs = any_dirs or src_path.is_dir()
            copy_and_register_files(src_path, dest_path, document)
        else:
            logger.warning(
                'The path "{}", defined in {} "extra-media", '
                'isn\'t a valid path.'.format(
                    extract_media_path, source_file
                )
            )
        if any_dirs:
            document.settings.env.note_reread()


class LinkedNotebookParser(NotebookParser):
    """A parser for .nblink files.

    The parser will replace the link file with the output from
    nbsphinx on the linked notebook. It will also add the linked
    file as a dependency, so that sphinx will take it into account
    when figuring out whether it should be rebuilt.

    The .nblink file is a JSON file with the following structure:

    {
        "path": "relative/path/to/notebook"
    }

    Optionally the "extra-media" key can be added, if your notebook includes
    any media, i.e. images. The value needs to be an array of strings,
    which are paths to the media files or directories.

    Further keys might be added in the future.
    """

    supported = 'linked_jupyter_notebook',

    def parse(self, inputstring: str, document: ddocument) -> None:
        """Parse the nblink file.

        Adds the linked file as a dependency, read the file, and
        pass the content to the nbshpinx.NotebookParser.
        """
        link = json.loads(inputstring)
        env = document.settings.env
        source_file = Path(env.docname)
        source_dir = source_file.parent

        abs_path = (env.srcdir / source_dir / link['path']).resolve()
        target_root = env.config.nbsphinx_link_target_root or Path.cwd()
        target = abs_path.relative_to(target_root, walk_up=True)

        extra_media = link.get('extra-media', None)
        if extra_media:
            collect_extra_media(extra_media, source_file, target, document)

        register_dependency(target, document)

        env.metadata[env.docname]['nbsphinx-link-target'] = target

        # Copy parser from nbsphinx for our cutom format
        try:
            formats = env.config.nbsphinx_custom_formats
        except AttributeError:
            pass
        else:
            formats.setdefault('.nblink', ['nbformat.reads', {'as_version': _ipynbversion}])

        try:
            include_file = io.FileInput(source_path=target, encoding='utf8')
        except UnicodeEncodeError:
            raise NotebookError(
                f'Problems with linked notebook "{env.docname}" path:\n'
                f'Cannot encode input file path "{target}" '
                "(wrong locale?)."
            )
        except OSError as error:
            
            raise NotebookError(
                f'Problems with linked notebook "{env.docname}" path:\n{io.error_string(error)}.'
            )

        try:
            rawtext = include_file.read()
        except UnicodeError as error:
            raise NotebookError(
                f'Problem with linked notebook "{env.docname}":\n{io.error_string(error)}'
            )
        return super().parse(rawtext, document)


def setup(app: Sphinx):
    """Initialize Sphinx extension."""
    app.setup_extension('nbsphinx')
    app.add_source_suffix('.nblink', 'linked_jupyter_notebook')
    app.add_source_parser(LinkedNotebookParser)
    app.add_config_value('nbsphinx_link_target_root', None, rebuild='env')

    return {'version': __version__, 'parallel_read_safe': True}
