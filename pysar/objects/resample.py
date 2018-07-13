############################################################
# Program is part of PySAR                                 #
# Copyright(c) 2018, Zhang Yunjun                          #
# Author:  Zhang Yunjun, 2018                              #
############################################################
# Recommend import:
#     from pysar.objects.resample import resample


try:
    import pyresample as pr
except ImportError:
    raise ImportError('Can not import pyresample!')

import sys
import numpy as np
from scipy.interpolate import RegularGridInterpolator as RGI
import multiprocessing
from pysar.utils import readfile, ptime


class resample:
    """
    Geometry Definition objects for geocoding using:
    1) pyresample (http://pyresample.readthedocs.org)
    2) scipy.interpolate.RegularGridInterpolator:
       (https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.RegularGridInterpolator.html)

    Example:
        res_obj = resample(lookupFile='./INPUTS/geometryGeo.h5', dataFile='velocity.h5')
        res_obj = resample(lookupFile='./INPUTS/geometryRadar.h5', dataFile='temporalCoherence.h5')
    """

    def __init__(self, lookupFile, dataFile, SNWE=None, laloStep=None, processor=None):
        self.file = lookupFile
        self.dataFile = dataFile
        self.SNWE = SNWE
        self.laloStep = laloStep
        self.processor = processor

    def open(self):
        """Prepare aux data before interpolation operation"""
        self.lut_metadata = readfile.read_attribute(self.file)
        self.src_metadata = readfile.read_attribute(self.dataFile)

        if not self.processor:
            if 'Y_FIRST' in self.lut_metadata.keys():
                self.processor = 'scipy'
            else:
                self.processor = 'pyresample'

        # get src_def and dest_def, update SNWE and laloStep
        if self.processor == 'pyresample':
            if 'Y_FIRST' not in self.lut_metadata.keys():
                self.get_geometry_definition4radar_lookup_table()
            else:
                self.get_geometry_definition4geo_lookup_table()
            self.length, self.width = self.dest_def.lats.shape

        elif self.processor == 'scipy' and 'Y_FIRST' in self.lut_metadata.keys():
            self.prepare_regular_grid_interpolator()

    def resample(self, src_data, interp_method='nearest', fill_value=np.nan, nprocs=None, print_msg=True):
        """Run interpolation operation for input 2D/3D data
        Parameters: src_data      : 2D/3D np.array, source data to be geocoded
                    interp_method : string, nearest | linear
                    fill_value    : NaN or number
                    nprocs        : int, number of processes to be used
                    print_msg     : bool
        Returns:    geo_data      : 2D/3D np.array
        """
        # use pyresample
        if self.processor == 'pyresample':
            if len(src_data.shape) == 3:
                src_data = np.moveaxis(src_data, 0, -1)

            # resample source data into target data
            geo_data = self.run_pyresample(src_data=src_data,
                                           interp_method=interp_method,
                                           fill_value=fill_value,
                                           nprocs=nprocs,
                                           radius=None,
                                           print_msg=True)

            if len(geo_data.shape) == 3:
                geo_data = np.moveaxis(geo_data, -1, 0)

        # use scipy.interpolater.RegularGridInterpolator
        else:
            if print_msg:
                print('resampling using scipy.interpolate.RegularGridInterpolator ...')
            if len(src_data.shape) == 3:
                geo_data = np.empty((src_data.shape[0], self.length, self.width), src_data.dtype)
                prog_bar = ptime.progressBar(maxValue=src_data.shape[0])
                for i in range(src_data.shape[0]):
                    geo_data[i, :, :] = self.run_regular_grid_interpolator(src_data=src_data[i, :, :],
                                                                           interp_method=interp_method,
                                                                           fill_value=fill_value,
                                                                           print_msg=True)
                    prog_bar.update(i+1)
                prog_bar.close()
            else:
                geo_data = self.run_regular_grid_interpolator(src_data=src_data,
                                                              interp_method=interp_method,
                                                              fill_value=fill_value,
                                                              print_msg=True)
        return geo_data

    def prepare_regular_grid_interpolator(self):
        """Prepare aux data for RGI module"""
        # source points in regular grid
        src_length = int(self.src_metadata['LENGTH'])
        src_width = int(self.src_metadata['WIDTH'])
        self.src_pts = (np.arange(src_length), np.arange(src_width))

        # destination points
        dest_y = readfile.read(self.file, datasetName='azimuthCoord')[0]
        dest_x = readfile.read(self.file, datasetName='rangeCoord')[0]
        if 'SUBSET_XMIN' in self.src_metadata.keys():
            print('input data file was cropped before.')
            dest_y[dest_y != 0.] -= float(self.src_metadata['SUBSET_YMIN'])
            dest_x[dest_x != 0.] -= float(self.src_metadata['SUBSET_XMIN'])
        self.interp_mask = np.multiply(np.multiply(dest_y > 0, dest_y < src_length),
                                       np.multiply(dest_x > 0, dest_x < src_width))
        self.dest_pts = np.hstack((dest_y[self.interp_mask].reshape(-1, 1),
                                   dest_x[self.interp_mask].reshape(-1, 1)))

        # destimation data size
        self.length = int(self.lut_metadata['LENGTH'])
        self.width = int(self.lut_metadata['WIDTH'])
        lat0 = float(self.lut_metadata['Y_FIRST'])
        lon0 = float(self.lut_metadata['X_FIRST'])
        lat_step = float(self.lut_metadata['Y_STEP'])
        lon_step = float(self.lut_metadata['X_STEP'])
        self.laloStep = (lat_step, lon_step)
        self.SNWE = (lat0 + lat_step * (self.length - 1),
                     lat0,
                     lon0,
                     lon0 + lon_step * (self.width - 1))

    def run_regular_grid_interpolator(self, src_data, interp_method='nearest', fill_value=np.nan, print_msg=True):
        """Interpolate 2D matrix"""
        # prepare interpolation function
        rgi_func = RGI(self.src_pts,
                       src_data,
                       method=interp_method,
                       bounds_error=False,
                       fill_value=fill_value)

        # prepare output matrix
        geo_data = np.empty((self.length, self.width), src_data.dtype)
        geo_data.fill(fill_value)

        # interpolate output matrix
        geo_data[self.interp_mask] = rgi_func(self.dest_pts)
        return geo_data


    def get_geometry_definition4radar_lookup_table(self):
        """Get src_def and dest_def for lookup table from ISCE, DORIS"""

        def mark_lalo_anomoly(lat, lon):
            """mask pixels with abnormal values (0, etc.)
            This is found on sentinelStack multiple swath lookup table file.
            """
            # ignore pixels with zero value
            zero_mask = np.multiply(lat != 0., lon != 0.)

            # ignore anomaly non-zero values 
            # by get the most common data range (d_min, d_max) based on histogram
            mask = np.array(zero_mask, np.bool_)
            for data in [lat, lon]:
                hist, bin_edges = np.histogram(data[zero_mask], bins=10)

                # if there is anomaly, histogram won't be evenly distributed
                if np.max(hist) > np.sum(zero_mask) * 0.7:
                    idx = np.argmax(hist)
                    d_mean = (bin_edges[idx] + bin_edges[idx+1]) / 2.
                    bin_step = bin_edges[1] - bin_edges[0]
                    d_min = d_mean - bin_step
                    d_max = d_mean + bin_step
                    mask *= np.multiply(data > d_min, data < d_max)
            lat[mask == 0] = 90.
            lon[mask == 0] = 0.
            return lat, lon, mask

        # radar2geo
        if 'Y_FIRST' not in self.src_metadata.keys():

            # src_def
            src_lat = readfile.read(self.file, datasetName='latitude')[0]
            src_lon = readfile.read(self.file, datasetName='longitude')[0]
            src_lat, src_lon, mask = mark_lalo_anomoly(src_lat, src_lon)

            # laloStep
            SNWE = (np.nanmin(src_lat[mask]), np.nanmax(src_lat[mask]),
                    np.nanmin(src_lon[mask]), np.nanmax(src_lon[mask]))
            if self.laloStep is None:
                self.laloStep = ((SNWE[0] - SNWE[1]) / (src_lat.shape[0] - 1),
                                 (SNWE[3] - SNWE[2]) / (src_lat.shape[1] - 1))
            else:
                self.laloStep = (abs(self.laloStep[0]) * -1.,
                                 abs(self.laloStep[1]) * 1.)
            print('output pixel size in (lat, lon) in degree: {}'.format(self.laloStep))

            # SNWE
            if self.SNWE is None:
                lat_num = int((SNWE[0] - SNWE[1]) / self.laloStep[0] + 0.5) + 1
                lon_num = int((SNWE[3] - SNWE[2]) / self.laloStep[1] + 0.5) + 1
                SNWE = (SNWE[1] + self.laloStep[0] * (lat_num - 1),
                        SNWE[1],
                        SNWE[2],
                        SNWE[2] + self.laloStep[1] * (lon_num - 1))
                self.SNWE = SNWE
            print('output area extent in (S N W E) in degree: {}'.format(self.SNWE))

            # dest_def
            lat_num = int((self.SNWE[0] - self.SNWE[1]) / self.laloStep[0] + 0.5) + 1
            lon_num = int((self.SNWE[3] - self.SNWE[2]) / self.laloStep[1] + 0.5) + 1
            self.SNWE = (self.SNWE[1] + self.laloStep[0] * (lat_num - 1),
                         self.SNWE[1],
                         self.SNWE[2],
                         self.SNWE[2] + self.laloStep[1] * (lon_num - 1))
            dest_lat, dest_lon = np.mgrid[self.SNWE[1]:self.SNWE[0]:lat_num*1j,
                                          self.SNWE[2]:self.SNWE[3]:lon_num*1j]

            self.src_def = pr.geometry.SwathDefinition(lons=src_lon, lats=src_lat)
            self.dest_def = pr.geometry.GridDefinition(lons=dest_lon, lats=dest_lat)

        # geo2radar
        else:
            # dest_def
            dest_lat = readfile.read(self.file, datasetName='latitude')[0]
            dest_lon = readfile.read(self.file, datasetName='longitude')[0]
            dest_lat, dest_lon, mask = mark_lalo_anomoly(dest_lat, dest_lon)

            # src_def
            lat0 = float(self.src_metadata['Y_FIRST'])
            lon0 = float(self.src_metadata['X_FIRST'])
            lat_step = float(self.src_metadata['Y_STEP'])
            lon_step = float(self.src_metadata['X_STEP'])
            lat_num = int(self.src_metadata['LENGTH'])
            lon_num = int(self.src_metadata['WIDTH'])
            lat1 = lat0 + lat_step * (lat_num - 1)
            lon1 = lon0 + lon_step * (lon_num - 1)

            src_lat, src_lon = np.mgrid[lat0:lat1:lat_num * 1j,
                                        lon0:lon1:lon_num * 1j]

            self.src_def = pr.geometry.GridDefinition(lons=src_lon, lats=src_lat)
            self.dest_def = pr.geometry.SwathDefinition(lons=dest_lon, lats=dest_lat)

    def get_geometry_definition4geo_lookup_table(self):
        """Get src_def and dest_def for lookup table from Gamma and ROI_PAC."""
        def project_yx2lalo(yy, xx, SNWE, laloScale):
            """scale/project coordinates in pixel number into lat/lon
            based on bbox and step
            """
            lats = SNWE[1] + yy * laloScale[0]
            lons = SNWE[2] + xx * laloScale[1]
            lats[np.isnan(lats)] = 90.
            lats[np.isnan(lons)] = 90.
            lons[lats == 90.] = 0
            return lats, lons

        # radar2geo
        if 'Y_FIRST' not in self.src_metadata.keys():
            lat0 = float(self.lut_metadata['Y_FIRST'])
            lon0 = float(self.lut_metadata['X_FIRST'])
            lat_step = float(self.lut_metadata['Y_STEP'])
            lon_step = float(self.lut_metadata['X_STEP'])
            lat_num = int(self.lut_metadata['LENGTH'])
            lon_num = int(self.lut_metadata['WIDTH'])

            # laloStep
            if self.laloStep is None:
                self.laloStep = (lat_step, lon_step)
            else:
                self.laloStep = (abs(self.laloStep[0]) * -1.,
                                 abs(self.laloStep[1]) * 1.)
            print('output pixel size in (lat, lon) in degree: {}'.format(self.laloStep))

            # SNWE
            if self.SNWE is None:
                self.SNWE = (lat0 + lat_step * (lat_num - 1),
                             lat0,
                             lon0,
                             lon0 + lon_step * (lon_num - 1))
            dest_box = (int((self.SNWE[2] - lon0) / lon_step + 0.5),
                        int((self.SNWE[1] - lat0) / lat_step + 0.5),
                        int((self.SNWE[3] - lon0) / lon_step + 0.5) + 1,
                        int((self.SNWE[0] - lat0) / lat_step + 0.5) + 1)
            self.SNWE = (lat0 + lat_step * (dest_box[3] - 1),
                         lat0 + lat_step * dest_box[1],
                         lon0 + lon_step * dest_box[0],
                         lon0 + lon_step * (dest_box[2] - 1))
            print('output area extent in (S N W E) in degree: {}'.format(self.SNWE))

            # src_y/x
            length = int(self.src_metadata['LENGTH'])
            width = int(self.src_metadata['WIDTH'])
            src_y, src_x = np.mgrid[0:length-1:length*1j,
                                    0:width-1:width*1j]

            # dest_y/x
            dest_y = readfile.read(self.file, datasetName='azimuthCoord', box=dest_box)[0]
            dest_x = readfile.read(self.file, datasetName='rangeCoord', box=dest_box)[0]
            if 'SUBSET_XMIN' in self.src_metadata.keys():
                print('input data file was cropped before.')
                dest_y[dest_y != 0.] -= float(self.src_metadata['SUBSET_YMIN'])
                dest_x[dest_x != 0.] -= float(self.src_metadata['SUBSET_XMIN'])

            # Convert y/x to lat/lon to be able to use pyresample.geometryDefinition
            commMask = np.multiply(np.multiply(dest_y > 0, dest_y < length),
                                   np.multiply(dest_x > 0, dest_x < width))
            idx_row, idx_col = np.where(commMask)
            commSNWE = (self.SNWE[1] + lat_step * np.max(idx_row),
                        self.SNWE[1] + lat_step * np.min(idx_row),
                        self.SNWE[2] + lon_step * np.min(idx_col),
                        self.SNWE[2] + lon_step * np.max(idx_col))
            laloScale = ((commSNWE[0] - commSNWE[1]) / length,
                         (commSNWE[3] - commSNWE[2]) / width)
            src_lat, src_lon = project_yx2lalo(src_y, src_x, commSNWE, laloScale)
            dest_y[dest_y == 0.] = np.nan
            dest_x[dest_x == 0.] = np.nan
            dest_lat, dest_lon = project_yx2lalo(dest_y, dest_x, commSNWE, laloScale)

            # src_def and dest_def
            self.src_def = pr.geometry.GridDefinition(lons=src_lon, lats=src_lat)
            self.dest_def = pr.geometry.SwathDefinition(lons=dest_lon, lats=dest_lat)

        # geo2radar
        else:
            # src_y/x
            raise NotImplementedError('Not implemented yet for GAMMA and ROIPAC products')

    def get_radius_of_influence(self, ratio=3):
        """Get radius of influence based on the lookup table resolution in lat/lon direction"""
        earth_radius = 6371.0e3
        # Get lat/lon resolution/step in meter
        lat_c = np.nanmean(self.dest_def.lats)
        lat_step = self.laloStep[1] * np.pi/180.0 * earth_radius
        lon_step = self.laloStep[0] * np.pi/180.0 * earth_radius * np.cos(lat_c * np.pi/180)
        radius = np.max(np.abs([lat_step, lon_step])) * ratio
        return radius

    def get_segment_number(self, unit_size=1e6):
        num_segment = int(self.dest_def.size / unit_size + 0.5)
        return num_segment

    def run_pyresample(self, src_data, interp_method='nearest', fill_value=np.nan, nprocs=None,
                       radius=None, print_msg=True):
        """
        Resample input src_data into dest_data
        Parameters: src_data : 2D np.array
                    interp_method : str,
                    fill_value : number
                    nprocs : int
                    print_msg : bool
        Returns:    dest_data
        Example:    dest_data = reObj.resample(src_data, src_def, dest_def,
                                               interp_method=inps.interpMethod,
                                               fill_value=np.fillValue, nprocs=4)
        """
        if not radius:
            # geo2radar
            if 'Y_FIRST' in self.src_metadata.keys():
                radius = 100e3
            # radar2geo
            else:
                radius = self.get_radius_of_influence()

        if not nprocs:
            nprocs = multiprocessing.cpu_count()

        num_segment = self.get_segment_number()

        if interp_method.startswith('near'):
            if print_msg:
                print(('nearest resampling with kd_tree ({} segments) '
                       'using {} processor cores ...').format(num_segment, nprocs))
            dest_data = pr.kd_tree.resample_nearest(self.src_def,
                                                    src_data,
                                                    self.dest_def,
                                                    nprocs=nprocs,
                                                    fill_value=fill_value,
                                                    radius_of_influence=radius,
                                                    segments=num_segment,
                                                    epsilon=0.5)

        elif interp_method.endswith('linear'):
            if print_msg:
                print('bilinear resampling using {} processor cores ...'.format(nprocs))
            dest_data = pr.bilinear.resample_bilinear(src_data,
                                                      self.src_def,
                                                      self.dest_def,
                                                      nprocs=nprocs,
                                                      fill_value=fill_value,
                                                      radius=radius,
                                                      neighbours=32,
                                                      epsilon=0)

        # for debug
        debug_mode = False
        if debug_mode:
            import matplotlib.pyplot as plt
            fig, ((ax11, ax12, ax13), (ax21, ax22, ax23)) = plt.subplots(nrows=2, ncols=3, figsize=(12, 8))
            dest_lats = np.array(self.dest_def.lats); dest_lats[dest_lats == 90.] = np.nan
            dest_lons = np.array(self.dest_def.lons); dest_lons[dest_lons == 0.] = np.nan
            im = ax11.imshow(self.src_def.lats);    fig.colorbar(im, ax=ax11);   ax11.set_title('src_lats')
            im = ax12.imshow(self.src_def.lons);    fig.colorbar(im, ax=ax12);   ax12.set_title('src_lons')
            im = ax13.imshow(src_data);             fig.colorbar(im, ax=ax13);   ax13.set_title('src_data')
            im = ax21.imshow(dest_lats);            fig.colorbar(im, ax=ax21);   ax21.set_title('dest_lats')
            im = ax22.imshow(dest_lons);            fig.colorbar(im, ax=ax22);   ax22.set_title('dest_lons')
            im = ax23.imshow(dest_data);            fig.colorbar(im, ax=ax23);   ax23.set_title('dest_data')
            plt.show()

        return dest_data
