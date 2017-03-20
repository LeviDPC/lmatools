"""
USAGE
python cell-lasso-stats.py path_to_lasso_log.txt path_to_sort_results outdir

path_to_lasso_log.txt is a path to a log file of cell lassos in json format,
    as created by the grid analysis GUI notebook that operates on the NetCDF
    flash files.

path_to_sort_results is a path to a standard directory of flash sorting
    results produced by lmatools, and which contains gridded flash counts 
    and hdf5 flash files on the usual paths:
        flash_sort_results/
            grid_files/yyyy/mon/dd/*.nc
            h5_files/yyyy/mon/dd/*.h5

outdir is created in within the figures-length folder inside path_to_sort_results
    This way multiple runs with different lassos on the same dataset are stored
    together. figures-length is created if it does not exist.
    
EXAMPLE
python lmatools/examples/lasso/cell-lasso-stats.py lmatools/testing/test_gen_autorun_DBSCAN_LassoLog.txt lmatools/sampledata/flashsort-solution/ lmatoolstest
"""

import os, sys, errno
from datetime import timedelta

import numpy as np
from numpy.lib.recfunctions import append_fields, stack_arrays
import matplotlib.pyplot as plt

from lmatools.grid.grid_collection import LMAgridFileCollection
from lmatools.flash_stats import plot_energy_from_area_histogram, get_energy_spectrum_bins,  bin_center

from lmatools.lasso.energy_stats import flash_size_stats, plot_flash_stat_time_series
from lmatools.lasso.length_stats import FractalLengthProfileCalculator
from lmatools.lasso.cell_lasso_util import read_poly_log_file, polys_to_bounding_box, h5_files_from_standard_path, nc_files_from_standard_path
from lmatools.lasso.cell_lasso_timeseries import TimeSeriesPolygonFlashSubset
from lmatools.lasso import EmpericalChargeDensity as cd ###NEW

# =====
# Read polygon data and configure output directories
# =====
polylog = sys.argv[1]
polys, t_edges_polys = read_poly_log_file(polylog)
t_start, t_end = min(t_edges_polys), max(t_edges_polys)
dt = timedelta(minutes=30)

path_to_sort_results = sys.argv[2]  
outdir_name_from_user = sys.argv[3]      
outdir=os.path.join(path_to_sort_results, 'figures-length/', outdir_name_from_user)
try:
    os.makedirs(outdir)
except OSError as exc:
    if exc.errno == errno.EEXIST and os.path.isdir(outdir):
        pass

print path_to_sort_results
# =====
# Load data from HDF5 files into a time series of events and flashes
# =====
h5_filenames = h5_files_from_standard_path(path_to_sort_results, t_start, t_end)
flashes_in_poly = TimeSeriesPolygonFlashSubset(h5_filenames, t_start, t_end, dt, 
                        min_points=10, polys=polys, t_edges_polys=t_edges_polys)
events_series, flashes_series = flashes_in_poly.get_event_flash_time_series()

# filter out flashes based on non-space-time criteria
# filter out events not part of flashes - NOT IMPLEMENTED
def gen_filtered_time_series(events_series, flashes_series, bounds):
    for events, flashes in zip(events_series, flashes_series):
        ev_good = np.ones(events.shape, dtype=bool)
        fl_good = np.ones(flashes.shape, dtype=bool)
        
        for k, (v_min, v_max) in bounds.items():
            if k in events.dtype.names:
                ev_good &= (events[k] >= v_min) & (events[k] <= v_max)
            if k in flashes.dtype.names:
                fl_good &= (flashes[k] >= v_min) & (flashes[k] <= v_max)
        events_filt, flashes_filt = (events[ev_good], flashes[fl_good])
        
        yield (events_filt, flashes_filt)
        
bounds={'area':(0.0, 1.0e10)} # used to remove flashes not meeting certain critera
filtered_time_series = gen_filtered_time_series(events_series, flashes_series, bounds)
events_series, flashes_series = zip(*filtered_time_series)

# =====
# plot the raw flash locations
# =====
class FlashCentroidPlotter(object):
    def __init__(self, t_sec_min, t_sec_max):
        self.t_sec_min, self.t_sec_max = t_sec_min, t_sec_max
        self.fig = plt.figure(figsize=(11,11))
        self.evax = self.fig.add_subplot(111)
        self.evax.set_ylabel('Latitude (deg)')
        self.evax.set_xlabel('Longitude (deg)')
        self.evax.set_title('Flash event-weighted centroid, \n{0} to {1} seconds'.format(
            t_sec_min, t_sec_max
            # date_min.strftime('%Y-%m-%d %H%M:%S'),
            # date_max.strftime('%Y-%m-%d %H%M:%S')
        ))
        self.colorbar = None
        self.scatters = []

    def plot(self, flashes):
        scart = self.evax.scatter(flashes['init_lon'], flashes['init_lat'], 
                      c=flashes['start'], s=4, cmap='viridis',
                      vmin=self.t_sec_min, vmax=self.t_sec_max, edgecolor='none')
        if self.colorbar is None:
            self.colorbar = plt.colorbar(scart, ax=self.evax)
        self.scatters.append(scart)

flash_location_plotter = FlashCentroidPlotter(min(flashes_in_poly.t_edges_seconds),
                                              max(flashes_in_poly.t_edges_seconds))
for flashes in flashes_series:
    flash_location_plotter.plot(flashes)
flash_ctr_filename = 'flash_ctr_{0}_{1}.png'.format(t_start.strftime('%y%m%d%H%M%S'),
                                                    t_end.strftime('%y%m%d%H%M%S'))
flash_location_plotter.fig.savefig(os.path.join(outdir, flash_ctr_filename))


# =======================================
# Loop over each window in the time series and calculate some
# aggregate flash statistical properties
# =======================================

# Set up fractal length calcuations and channel height profiles
D = 1.5
b_s = 200.0
max_alt, d_alt = 20.0, 0.5
alt_bins = np.arange(0.0,max_alt+d_alt, d_alt)
length_profiler = FractalLengthProfileCalculator(D, b_s, alt_bins)

def gen_flash_summary_time_series(events_series, flashes_series, length_profiler):
    for events, flashes in zip(events_series, flashes_series):
        # reduce all flashes in this time interval to representative moments
        size_stats = flash_size_stats(flashes)
        # for each flash, get a dictionary with 2D and 3D fractal lengths.
        # also includes the volume and point triangulation data.
        per_flash_data, IC_profile, CG_profile = length_profiler.process_events_flashes(events, flashes)
        yield size_stats, IC_profile, CG_profile


time_series = gen_flash_summary_time_series(events_series, flashes_series, 
                                            length_profiler)
size_stats, IC_profiles, CG_profiles = zip(*time_series)

size_stats = stack_arrays(size_stats)
iso_start, iso_end = flashes_in_poly.t_edges_to_isoformat(as_start_end=True)
# assume here that iso_start and iso_end are identical-length strings, 
# as they should be if the iso format is worth anything.
size_stats = append_fields(size_stats, ('start_isoformat','end_isoformat'),
                                         data=(iso_start, iso_end), usemask=False)
   
# =====
# Write flash size stats data (see harvest_flash_timeseries) and plot moment time series
# =====
def write_size_stat_data(outfile, size_stats):
    stat_keys = ('start_isoformat','end_isoformat', 'number', 'mean', 'variance', 
                 'skewness', 'kurtosis', 'energy', 'energy_per_flash')
    header = "# start_isoformat, end_isoformat, number, mean, variance, skewness, kurtosis, energy, energy_per_flash\n"
    line_template = "{0}, {1}, {2}, {3}, {4}, {5}, {6}, {7}, {8}\n"
    f = open(outfile, 'w')
    #for start_t, end_t, stats in zip(starts, ends, size_stats):
    f.write(header)
    for stats in size_stats:
        stat_vals = [stats[k] for k in stat_keys]
        line = line_template.format(*stat_vals)
        f.write(line)
    f.close()    
    
stats_filename = os.path.join(outdir,'flash_stats.csv')
stats_figure = os.path.join(outdir,'flash_stats_{0}_{1}.pdf'.format(t_start.strftime('%y%m%d%H%M%S'),
                                                    t_end.strftime('%y%m%d%H%M%S')))
write_size_stat_data(stats_filename, size_stats)
fig = plot_flash_stat_time_series(flashes_in_poly.base_date, flashes_in_poly.t_edges, size_stats)
fig.savefig(stats_figure)
    
# =====
# Write profile data to file and plot profile time series
# =====
durations_min = (flashes_in_poly.t_edges_seconds[1:] - flashes_in_poly.t_edges_seconds[:-1])/60.0 #minutes
fig_kwargs = dict(label_t_every=1800.)            

IC_norm_profiles = length_profiler.normalize_profiles(IC_profiles, durations_min)
CG_norm_profiles = length_profiler.normalize_profiles(CG_profiles, durations_min)

outfile_base = os.path.join(outdir,
                'D-{0:3.1f}_b-{1:4.2f}_length-profiles'.format(D,b_s)
                )
            
IC_fig = length_profiler.make_time_series_plot(flashes_in_poly.base_date,
            flashes_in_poly.t_edges_seconds,
            *IC_norm_profiles, **fig_kwargs)
CG_fig = length_profiler.make_time_series_plot(flashes_in_poly.base_date,
            flashes_in_poly.t_edges_seconds,
            *CG_norm_profiles, **fig_kwargs)
if 'CG' in flashes_series[0].dtype.names:
    length_profiler.write_profile_data(flashes_in_poly.base_date, flashes_in_poly.t_edges_seconds, 
                outfile_base, *IC_norm_profiles, partition_kind='IC')
    length_profiler.write_profile_data(flashes_in_poly.base_date, flashes_in_poly.t_edges_seconds, 
                outfile_base, *CG_norm_profiles, partition_kind='CG')
    CG_fig.savefig(outfile_base+'_CG.pdf')
    IC_fig.savefig(outfile_base+'_IC.pdf')
else:
    # no CG data and so the CG profiles should be empty. No need to save them
    length_profiler.write_profile_data(flashes_in_poly.base_date, flashes_in_poly.t_edges_seconds, 
                outfile_base, *IC_norm_profiles, partition_kind='total')
    IC_fig.savefig(outfile_base+'_total.pdf')

# =====
# Energy spectrum plots
# ====

footprint_bin_edges = get_energy_spectrum_bins()
spectrum_save_file_base = os.path.join(outdir, 'energy_spectrum_{0}_{1}.pdf')
for flashes, t0, t1 in zip(flashes_series, flashes_in_poly.t_edges[:-1], flashes_in_poly.t_edges[1:]):
    histo, edges = np.histogram(flashes['area'], bins=footprint_bin_edges)
    spectrum_save_file = spectrum_save_file_base.format(t0.strftime('%y%m%d%H%M%S'),
                                                        t1.strftime('%y%m%d%H%M%S'))
    plot_energy_from_area_histogram(histo, edges, 
                    save=spectrum_save_file, duration=(t1-t0).total_seconds())

# =========================================#
# Energy Spectrum from charge density:     # ADDED 161121
# =========================================# 
import matplotlib.pyplot as plt
import matplotlib
cmap_en = plt.cm.gist_heat

norm = matplotlib.colors.Normalize(
     vmin=(np.asarray(flashes_in_poly.t_edges).astype(float).min()),
     vmax=(np.asarray(flashes_in_poly.t_edges).astype(float).max()))

s_m = plt.cm.ScalarMappable(cmap=cmap_en,norm=norm)
s_m.set_array([])

plt.figure(figsize=(14,9))
cmap = plt.cm.Reds_r
spectrum_save_file_base_en = os.path.join(outdir, 'energy_spectrum_estimate_{0}_{1}.pdf')

for f, (flashes, t0, t1) in enumerate(zip(flashes_series, flashes_in_poly.t_edges[:-1], flashes_in_poly.t_edges[1:])):                    
    ###Now for charge densities approximated: #####
    random = np.random.randn(flashes['area'].shape[0])*0.8e3 + 4.1e3#3.5e3
    distance = np.abs(random) #Generates random distance between capacitor plates.
    density = cd.rho_retrieve(flashes['area'], distance)
    rho,w = density.calculate()
    flash_1d_extent = bin_center(np.sqrt(footprint_bin_edges))  
    histo_cd, edges_cd = np.histogram(np.sqrt(flashes['area']), bins=np.sqrt(footprint_bin_edges), weights=np.nan_to_num(w))
    # plot_energy_from_charge(histo_cd, edges_cd, w,
    #                 save=spectrum_save_file)
    
    spectrum_save_file_en = spectrum_save_file_base_en.format(t0.strftime('%y%m%d%H%M%S'),
                                                        t1.strftime('%y%m%d%H%M%S'))
                                                        
    plt.loglog(flash_1d_extent[:], histo_cd/np.sqrt(flash_1d_extent),color=s_m.to_rgba(t0),alpha=0.8);
    plt.ylim(1e7,1e13) 
    # plt.xlim(plt.xlim()[::-1])
    plt.xlim(1e2,1e-1)

wavenumber = (2.*np.pi)/flash_1d_extent
inertialsubrange = 10**6 * (wavenumber*0.0002)**(-5.0/3.0)
cbar = plt.colorbar(s_m)
plt.loglog(flash_1d_extent, inertialsubrange,'k-',alpha=0.5);
plt.title(spectrum_save_file_en.split('/')[-1].split('.')[0])
plt.xlabel(r'Flash width ($\sqrt{A_h}$, $km$)')
plt.ylabel(r'Energy ($J$)')
plt.savefig(spectrum_save_file_en)
plt.close()


# =====
# NetCDF grid processing
# =====
field_file = ('flash_extent', 'flash_init', 'source', 'footprint')
field_names = ('flash_extent', 'flash_initiation', 'lma_source', 'flash_footprint')
field_labels = ('Flash coverage (count per pixel)', 'Flash initation (count per pixel)', 'VHF Sources (count per pixel)', 'Average area (km^2)' )
grid_ranges = ((1, 1000), (1,100), (1, 100000),  (1, 100000.0))
field_ids_to_run = (0, 1, 2, 3)

def plot_lasso_grid_subset(fig,datalog, t,xedge,yedge,data,grid_lassos,field_name,basedate,grid_range, axis_range):
    from scipy.stats import scoreatpercentile
    from matplotlib.cm import get_cmap
    import matplotlib.colors
    cmap = get_cmap('cubehelix')
    norm=matplotlib.colors.LogNorm()
    
    x = (xedge[1:]+xedge[:-1])/2.0
    y = (yedge[1:]+yedge[:-1])/2.0

    xmesh, ymesh = np.meshgrid(x, y)

    assert (xmesh.shape == ymesh.shape == data.shape)

    N = data.size

    # in these grids, Y moves along the zeroth index, X moves along the first index

    a = np.empty((N,), dtype=[('t','f4'), ('lon', 'f4'), ('lat','f4'), (field_name, data.dtype)])
    a['t'] = (t-basedate).total_seconds() + 1.0 # add a bit of padding to make sure it's within the time window
    a['lat'] = ymesh.flatten()
    a['lon'] = xmesh.flatten()
    a[field_name] = data.flatten()

    nonzero = a[field_name] > 0
    good = grid_lassos.filter_mask(a)
    filtered = a[good]
    nonzero_filtered = a[good & nonzero]
    # set up an array for use in pcolor
    masked_nonzero_filtered = a.view(np.ma.MaskedArray)
    masked_nonzero_filtered.shape = data.shape
    masked_nonzero_filtered.mask = (good & nonzero)==False

    ax=fig.add_subplot(111)
    ax.set_title(str(t))
    art = ax.pcolormesh(xedge, yedge,
                        masked_nonzero_filtered[field_name], 
                        vmin=grid_range[0], vmax=grid_range[1], 
                        cmap=cmap, norm=norm)
    fig.colorbar(art)
    ax.axis(axis_range)

    if nonzero_filtered.size > 0:
        v = filtered[field_name]
        vnz = nonzero_filtered[field_name]
        percentiles = scoreatpercentile(vnz, (5,50,95))
        row = map(str, (t.isoformat(), v.max(), v.sum())+tuple(percentiles))
    else:
        row = map(str, (t, 0 , 0, 0.0, 0.0, 0.0))
    datalog.write(', '.join(row)+'\n')

    

for field_id in field_ids_to_run:
    nc_filenames = nc_files_from_standard_path(path_to_sort_results, 
                        field_file[field_id], 
                        min(flashes_in_poly.t_edges), 
                        max(flashes_in_poly.t_edges))

    field_name = field_names[field_id]
    grid_range=grid_ranges[field_id]

    fig_outdir = os.path.join(outdir,'grids_{0}'.format(field_name))
    try:
      os.makedirs(fig_outdir)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(fig_outdir):
            pass

    GC = LMAgridFileCollection(nc_filenames, field_name, x_name='longitude', y_name='latitude')
    grid_lassos = flashes_in_poly.grid_lassos
    basedate = flashes_in_poly.base_date
    
    datalog_name = os.path.join(fig_outdir, '{0}_{1}.csv'.format(field_name, basedate.strftime('%Y%m%d')))
    datalog = open(datalog_name,'w')
    datalog.write('time (ISO), max count per grid box, sum of all grid boxes, 5th percentile, 50th percentile, 95th percentile\n')
    
    fig=plt.figure()
    lon_range, lat_range = polys_to_bounding_box(flashes_in_poly.polys)
    axis_range = lon_range+lat_range
    
    for t, xedge, yedge, data in GC:
        if (t >= t_start) & (t <= t_end):
            plot_lasso_grid_subset(fig,datalog,t,xedge,yedge,data,grid_lassos,field_name,basedate,grid_range, axis_range)
            fig.savefig(os.path.join(fig_outdir, '{0}_{1}.png'.format(field_name, t.strftime('%Y%m%d_%H%M%S'))))
            fig.clf()

    datalog.close()
