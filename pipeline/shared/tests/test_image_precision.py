"""Encoded-source regressions; fixtures must not be reduced by Pillow first."""
import io,struct,sys,unittest,zlib
from pathlib import Path
from PIL import Image
from pipeline.shared.image_store import store_figure

class MemoryStore:
    def __init__(self):self.objects={}
    def put(self,key,data,mime):self.objects[key]=(data,mime)
    def get(self,key):return self.objects[key]

def chunk(kind,body):
    return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))

def png16(color,samples):
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,16,color,0,0,0))+
            chunk(b'IDAT',zlib.compress(b'\0'+struct.pack('>'+'H'*len(samples),*samples)))+chunk(b'IEND',b''))

def rgb16_tiff():
    count=10;extra=8+2+count*12+4;offset=extra+6
    entries=[(256,4,1,1),(257,4,1,1),(258,3,3,extra),(259,3,1,1),(262,3,1,2),
             (273,4,1,offset),(277,3,1,3),(278,4,1,1),(279,4,1,6),(284,3,1,1)]
    return (b'II'+struct.pack('<HIH',42,8,count)+b''.join(struct.pack('<HHII',*e) for e in entries)+
            struct.pack('<IHHHHHH',0,16,16,16,0x1234,0x5678,0x9abc))

class ImagePrecisionTests(unittest.TestCase):
    def store(self,raw,store):
        return store_figure({'id':'fixture','version':'v1'},{'src':'figures/test'},{},'.',store,
                            loader=lambda *_:(raw,'application/octet-stream'))
    def test_all_sixteen_bit_png_color_types_reject_before_writes(self):
        for color,samples in [(0,[0x1234]),(2,[0x1234,0x5678,0x9abc]),
                              (4,[0x1234,0x5678]),(6,[0x1234,0x5678,0x9abc,0xdef0])]:
            for low_bit in (0,1):
                with self.subTest(color=color,low_bit=low_bit):
                    raw=png16(color,[samples[0]+low_bit,*samples[1:]])
                    store=MemoryStore()
                    with self.assertRaisesRegex(ValueError,'High-bit-depth'):self.store(raw,store)
                    self.assertEqual(store.objects,{})
    def test_tiff_decoder_rgb_mode_does_not_prove_eight_bits(self):
        raw=rgb16_tiff();image=Image.open(io.BytesIO(raw))
        self.assertEqual(image.tag_v2[258],(16,16,16))
        store=MemoryStore()
        with self.assertRaisesRegex(ValueError,'Unsupported raster format'):self.store(raw,store)
        self.assertEqual(store.objects,{})
    def test_cmyk_jpeg_and_unapproved_formats_reject(self):
        for mode,fmt in [('CMYK','JPEG'),('RGB','TIFF'),('RGB','BMP')]:
            with self.subTest(mode=mode,format=fmt):
                out=io.BytesIO();Image.new(mode,(2,2)).save(out,format=fmt);store=MemoryStore()
                with self.assertRaisesRegex(ValueError,'Unsupported raster'):self.store(out.getvalue(),store)
                self.assertEqual(store.objects,{})
    def test_encoded_twelve_bit_jpeg_rejects_before_writes(self):
        out=io.BytesIO();Image.new('RGB',(2,2)).save(out,format='JPEG')
        raw=bytearray(out.getvalue());sof=raw.index(b'\xff\xc0')
        self.assertEqual(raw[sof+4],8);raw[sof+4]=12
        store=MemoryStore()
        # Pillow may reject unsupported SOF precision before our explicit
        # image.bits check. Either way, no conversion or upload is permitted.
        with self.assertRaises((ValueError,OSError)):self.store(bytes(raw),store)
        self.assertEqual(store.objects,{})
    def test_supported_rasters_preserve_decoded_pixels_and_alpha(self):
        for mode,fmt in [('1','PNG'),('L','PNG'),('LA','PNG'),('RGB','PNG'),('RGBA','PNG'),
                         ('P','PNG'),('RGB','JPEG'),('L','JPEG'),('P','GIF'),('RGBA','WEBP')]:
            with self.subTest(mode=mode,format=fmt):
                image=Image.new(mode,(2,2));out=io.BytesIO()
                if mode=='P':
                    image.putpalette([255,0,0,0,255,0]+[0,0,0]*254)
                    image.putpixel((1,1),1);image.info['transparency']=0
                elif mode=='RGBA':image.putpixel((0,0),(125,54,23,0));image.putpixel((1,1),(4,5,6,127))
                elif mode=='LA':image.putpixel((0,0),(125,0));image.putpixel((1,1),(34,127))
                image.save(out,format=fmt,**({'lossless':True,'exact':True} if fmt=='WEBP' else {}))
                raw=out.getvalue();store=MemoryStore();entry=self.store(raw,store)
                original=Image.open(io.BytesIO(raw)).convert('RGBA')
                result=Image.open(io.BytesIO(store.get(entry['object_key'])[0])).convert('RGBA')
                self.assertEqual(original.size,result.size);self.assertEqual(original.tobytes(),result.tobytes())

if __name__=='__main__':unittest.main()
