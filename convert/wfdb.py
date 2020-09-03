import argparse
import os
from os import scandir
import glob
import audata
import pandas as pd
import wfdb
import loinc


'''Todo:
    - finish LOINC mapping for MIMIC
    - build mapping metadata during conversion
       - .meta/mapping/numerics
       - .meta/mapping/waveforms
    - integrate clinical values from MIMIC III DB (requires DB credentials and DUA) 
    - rebuild time index from sample rate and basetime
'''

def convert_wfdb_numerics (h5f, num_head):
    '''
    Convert numerics using wfdb library
    '''
    record = wfdb.rdrecord(num_head, sampfrom = 0, sampto = None )
    df = pd.DataFrame(data = record.p_signal, columns = record.sig_name)
    h5f['Numerics/Vitals'] = df

    cols = list(record.sig_name)
    old_meta = h5f['Numerics/Vitals'].meta['columns']
    columns = {}
    MIMICIII_loinc = loinc.Loinc(external_mapping="MIMICIII")
    for idx, col in enumerate(cols):
        col_meta = {}
        col_meta['type'] = old_meta[col]['type']
        col_meta['LOINC'] = MIMICIII_loinc.numeric(col)
        col_meta['uom'] = record.units[idx] if record.units is not None else None
        col_meta['scale'] = 1
        col_meta['sample_rate'] = record.fs
        col_meta['fmt'] = record.fmt[idx] if record.fmt is not None else None
        col_meta['baseline'] = record.baseline[idx] if record.baseline is not None else None
        col_meta['base_datetime'] = str(record.base_datetime)
        columns[col] = col_meta

    new_meta = {'columns': columns}
    h5f['Numerics/Vitals'].meta = new_meta


def convert_wfdb_waveforms (h5f, wave_head):
    ''' Convert waveforms using wfdb library '''
    
    wave_record = wfdb.rdrecord(wave_head, sampfrom = 0, sampto = None )
    wave_df = pd.DataFrame(data = wave_record.p_signal, columns = wave_record.sig_name)
    h5f['Waveforms/Hemodynamics'] = wave_df

    cols = list(wave_record.sig_name)
    old_meta = h5f['Waveforms/Hemodynamics'].meta['columns']
    MIMICIII_loinc = loinc.Loinc(external_mapping="MIMICIII")

    columns = {}
    for idx, col in enumerate(cols):
        col_meta = {}
        col_meta['type'] = old_meta[col]['type']
        col_meta['LOINC'] = MIMICIII_loinc.waveform(col)
        col_meta['uom'] = wave_record.units[idx] if wave_record.units is not None else None
        col_meta['scale'] = 1
        col_meta['sample_rate'] = wave_record.fs
        col_meta['fmt'] = wave_record.fmt[idx] if wave_record.fmt is not None else None
        col_meta['baseline'] = wave_record.baseline[idx] if wave_record.baseline is not None else None
        col_meta['base_datetime'] = str(wave_record.base_datetime)
        columns[col] = col_meta
    new_meta = {'columns': columns}

    h5f['Waveforms/Hemodynamics'].meta = new_meta

def scantree(path):
    """Recursively yield DirEntry objects for given directory."""
    for entry in scandir(path):
        if entry.is_dir(follow_symlinks=False):
            yield from scantree(entry.path) 
        else:
            yield entry


def ccdef_from_wfdb(name, dest_path='', numerics=True, waveforms=True, clinical=False):
    
    ''' 
    First deterine if the source is a numeric or waveform record
    Then check for the existance of the wave/numeric counterpart

    For batch processing, supply either numeric or waveform header for each pair
    
    Conversion of clinical data (lab/micro, notes) requires a database connection to MIMIC III
    - this is disabled by default
    - clinical conversion also will require range specifiers - eg limit clinical data to the time span of the
      numerics/waveform record OR include all data in the database

    '''

    if name.split('.hea')[0][-1] == 'n':
        num_head = name.split('.hea')[0]
        print('Numerics header {}'.format(os.path.basename(num_head)))
        num_exists = True
        wave_head = num_head[:-1]
        if os.path.exists(wave_head+'.hea'):
            print('Corresponding waveform header {} exists'.format(os.path.basename(wave_head)))
            wave_exists = True
        else:
            print('No corresponding waveform header {}'.format(os.path.basename(wave_head)))
            wave_exists = False
    else:
        wave_head = name.split('.hea')[0]
        print('Waveform header {}'.format(os.path.basename(wave_head)))
        wave_exists = True
        num_head = wave_head + 'n'
        if os.path.exists(num_head + '.hea'):
            print('Corresponding numerics header {} exists'.format(os.path.basename(num_head)))
            num_exists = True
        else:
            print('No corresponding numerics header {}'.format(os.path.basename(num_head)))
            num_exists = False
    
    ''' Extract metadata from the wfdb numerics record '''
    
    if num_exists:
        record = wfdb.rdrecord(num_head, sampfrom = 0, sampto = None )
        base_dt = record.base_datetime
        base_name = os.path.basename(num_head)
    else:
        record = wfdb.rdrecord(wave_head, sampfrom = 0, sampto = None )
        base_dt = record.base_datetime 
        
    
    ''' Create output hdf5 file using audata and include top level metadata '''

    if wave_exists:
        base_name = os.path.basename(wave_head)
    else:
        base_name = os.path.basename(num_head)

    out_name = os.path.join(dest_path,base_name) + '.h5'
    print('Saving to file {}'.format(out_name))
    
    with audata.File.new(out_name, time_reference=base_dt, title=record.record_name, 
                   author='Mimic III matched dataset', organization = 'PhysioNet', overwrite=True) as f:
    
        f.hdf.create_group('Waveforms')
        f.hdf.create_group('Numerics')
        f.hdf.create_group('Clinical')

        ''' Extract data from the wfdb numerics record if present and numerics=True'''
        if (num_exists & numerics):
            convert_wfdb_numerics (f, num_head)
        
        ''' Extract from the wfdb waveforms record if present '''
        if (wave_exists & waveforms):
            convert_wfdb_waveforms (f, wave_head)

#        wv_head = wfdb.rdheader(wave_head, rd_segments=True )

#    return (record,wv_head)
    return
       

def convert_files (source, dest_path, numerics=True, waveforms=True, clinical=False, recursive=False):
    ''' 
    If source is a file, convert it
    If source is a path, convert all the master records in the top level path
    - master records have the pattern p#####.hea
    If recursive is True then crawl subfolders and convert files as above
    '''
    if os.path.isfile(source):
        print ('File {} is a file, converting'.format(source))
        # convert
    elif os.path.isdir(source):
        if recursive:
            print('Scanning path recursively')
            master_headers = [f for f in glob.glob(os.path.join(source, '**/p*.hea'),recursive=True)]
        else:
            print('Scanning path non-recursively')
            master_headers = [f for f in glob.glob(os.path.join(source, 'p*.hea'))]
        num_headers = [f for f in master_headers if f.split('.hea')[0][-1] == 'n']

        for file in num_headers:
            #covert
            print('Converting {}'.format(file))
            ccdef_from_wfdb(file, dest_path=dest_path, numerics=numerics,
                            waveforms=waveforms, clinical=clinical)
            

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    
    parser.add_argument('source', help = 'Source file in wfdb format or search path')
    parser.add_argument('dest', nargs='?', default=os.getcwd(), help = 'destination path')
    
    
    parser.add_argument('-w', '--waveforms', action='store_true', 
    help="Convert waveforms")
    parser.add_argument('-n', '--numerics', action='store_true', 
    help="Convert numerics")
    parser.add_argument('-c', '--clinical', action='store_true', 
    help="Convert clinical (requires database connection to MIMIC")
    parser.add_argument('-r', '--recursive', action='store_true', 
    help="Scan source path recursively and convert all master headers")
    
    
    args = parser.parse_args()

    if args.waveforms:
        print("Converting waveforms from {}, saving to {}".format(args.source,
                                                                  args.dest))
    convert_files(args.source, args.dest, numerics=args.numerics,
                  waveforms=args.waveforms, clinical=args.clinical, 
                  recursive=args.recursive)
        
    