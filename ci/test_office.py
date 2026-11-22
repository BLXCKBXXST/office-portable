import io, pathlib, tarfile, tempfile, unittest
import office

class ArchiveTests(unittest.TestCase):
    def test_rejects_paths_outside_workspace(self):
        for path in ('../escape','root/../../escape','/absolute','C:/escape','\\server\\escape'):
            with self.subTest(path=path), self.assertRaises(ValueError):office.clean_parts(path)
        self.assertEqual(office.clean_parts('root/crates/test.rs'),('root','crates','test.rs'))

    def test_source_extraction_never_follows_archive_links(self):
        parent=office.WORK/'test-data';parent.mkdir(parents=True,exist_ok=True)
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            file=tarfile.TarInfo('revision/src/lib.rs');payload=b'// original fixture\n';file.size=len(payload);archive.addfile(file,io.BytesIO(payload))
            link=tarfile.TarInfo('revision/escape');link.type=tarfile.SYMTYPE;link.linkname='../../outside';archive.addfile(link)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            root=pathlib.Path(temporary);office.untar(data.getvalue(),root)
            self.assertEqual((root/'src/lib.rs').read_bytes(),payload)
            self.assertFalse((root/'escape').exists())

    def test_asset_names_and_profile_sections_are_explicit(self):
        self.assertEqual(len(office.TITLES),4)
        self.assertEqual(set(office.SECTIONS),{'Settings','User','Cache','Logs','Recovery','Runtime','Temp'})
        self.assertIsNone(office.ALLOWED_RESOURCE.fullmatch('My personal document.docx'))
        self.assertIsNotNone(office.ALLOWED_RESOURCE.fullmatch('LICENSE-MIT'))

if __name__=='__main__':unittest.main()
