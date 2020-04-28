"""
Contains FitDistribution and Profile class, which are

important classes for fitting to various Continous and Discrete Probability
Distributions

Author:  Per A. Brodtkorb 2008
"""

import warnings
import scipy.stats as ss
from scipy.stats._distn_infrastructure import rv_frozen as _rv_frozen

from wafo.plotbackend import plotbackend
from wafo.misc import ecross, findcross
# from scipy._lib.six import string_types
import numdifftools as nd  # @UnresolvedImport
from scipy import special
from scipy.linalg import pinv2
from scipy import optimize

import numpy as np
from scipy.special import expm1, log1p as _log1p  # pylint: disable=no-name-in-module
from numpy import (arange, zeros, sqrt, exp,
                   asarray, nan, pi, isfinite, flatnonzero)


__all__ = ['Profile', 'FitDistribution']


floatinfo = np.finfo(float)
# The smallest representable positive number such that 1.0 + _EPS != 1.0.
_EPS = floatinfo.eps
_TINY = floatinfo.tiny
_XMAX = floatinfo.max


def log(x):
    """Log that silence log of zero warnings"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.log(x)


def log1p(x):
    """Log that silence log of zero warnings"""
    with np.errstate(divide='ignore', invalid='ignore'):
        return _log1p(x)


def _assert_warn(cond, msg):
    if not cond:
        warnings.warn(msg)


def _assert(cond, msg):
    if not cond:
        raise ValueError(msg)


def _assert_index(cond, msg):
    if not cond:
        raise IndexError(msg)


def _assert_not_implemented(cond, msg):
    if not cond:
        raise NotImplementedError(msg)


def _burr_link(x, logsf, phat, ix):
    c, d, loc, scale = phat
    log_cdf = log(-expm1(logsf))
    xn = (x - loc) / scale
    if ix == 1:
        return -log_cdf / log1p(xn**(-c))
    if ix == 0:
        return log1p(-exp(-log_cdf / d)) / log(xn)
    if ix == 2:
        return x - scale * exp(log1p(-exp(-log_cdf / d)) / c)
    if ix == 3:
        return (x - loc) / exp(log1p(-exp(-log_cdf / d)) / c)
    raise IndexError('Index to the fixed parameter is out of bounds')


def _expon_link(x, logsf, phat, ix):
    if ix == 1:
        return - (x - phat[0]) / logsf
    if ix == 0:
        return x + phat[1] * logsf
    raise IndexError('Index to the fixed parameter is out of bounds')


def _weibull_min_link(x, logsf, phat, ix):
    """Returns the parameter ix given x, logsf and all distribution parameters except ix

    Parameters
    ----------
    x : real scalar
        quantile of the distribution
    logsf: real scalar
        log survival probability
    phat: list of real scalars
        distribution parameters, [c, loc, scale]
    ix: scalar integer
        index to the distribution parameter to return. I.e. 0 means c, 1 is loc and 2 is scale
    """
    c, loc, scale = phat

    if ix == 0:
        return log(-logsf) / log((x - loc) / scale)
    if ix == 1:
        return x - scale * (-logsf) ** (1. / c)
    if ix == 2:
        return (x - loc) / (-logsf) ** (1. / c)
    raise IndexError('Index to the fixed parameter is out of bounds')


def _exponweib_link(x, logsf, phat, ix):
    a, c, loc, scale = phat
    log_cdf = -log(-expm1(logsf))
    xn = (x - loc) / scale
    if ix == 0:
        return - log_cdf / log(-expm1(-xn**c))
    if ix == 1:
        return log(-log1p(- log_cdf ** (1.0 / a))) / log(xn)
    if ix == 2:
        return x - (-log1p(- log_cdf ** (1.0 / a))) ** (1.0 / c) * scale
    if ix == 3:
        return (x - loc) / (-log1p(- log_cdf ** (1.0 / a))) ** (1.0 / c)
    raise IndexError('Index to the fixed parameter is out of bounds')


def _genpareto_link(x, logsf, phat, ix):
    # Reference
    # Stuart Coles (2004)
    # "An introduction to statistical modelling of extreme values".
    # Springer series in statistics
    _assert_not_implemented(ix != 0, 'link(x,logsf,phat,i) where i=0 is '
                            'not implemented!')
    c, loc, scale = phat
    if c == 0:
        return _expon_link(x, logsf, phat[1:], ix - 1)
    if ix == 2:
        # Reorganizing w.r.t.scale, Eq. 4.13 and 4.14, pp 81 in
        # Coles (2004) gives
        #   link = -(x-loc)*c/expm1(-c*logsf)
        return (x - loc) * c / expm1(-c * logsf)
    if ix == 1:
        return x + scale * expm1(c * logsf) / c
    raise IndexError('Index to the fixed parameter is out of bounds')


def _gumbel_r_link(x, logsf, phat, ix):
    loc, scale = phat
    loglog_cdf = log(-log(-expm1(logsf)))
    if ix == 1:
        return -(x - loc) / loglog_cdf
    if ix == 1:
        return x + scale * loglog_cdf
    raise IndexError('Index to the fixed parameter is out of bounds')


def _genextreme_link(x, logsf, phat, ix):
    _assert_not_implemented(ix != 0, 'link(x,logsf,phat,i) where i=0 is not '
                            'implemented!')
    c, loc, scale = phat
    if c == 0:
        return _gumbel_r_link(x, logsf, phat[1:], ix - 1)
    cdf = -expm1(logsf)

    loglog_cdf = log(-log(cdf))
    return _genpareto_link(x, loglog_cdf, (-c, loc, scale), ix)


def _genexpon_link(x, logsf, phat, ix):
    a, b, c, loc, scale = phat
    xn = (x - loc) / scale
    fact1 = (xn + expm1(-c * xn) / c)
    if ix == 0:
        return b * fact1 + logsf  # a
    if ix == 1:
        return (a - logsf) / fact1  # b
    if ix in [2, 3, 4]:
        raise NotImplementedError('Only implemented for index in [0,1]!')
    raise IndexError('Index to the fixed parameter is out of bounds')


def _rayleigh_link(x, logsf, phat, ix):
    if ix == 1:
        return x - phat[0] / sqrt(-2.0 * logsf)
    if ix == 0:
        return x - phat[1] * sqrt(-2.0 * logsf)
    raise IndexError('Index to the fixed parameter is out of bounds')


def _trunclayleigh_link(x, logsf, phat, ix):
    c, loc, scale = phat
    if ix == 0:
        xn = (x - loc) / scale
        return - 2 * logsf / xn - xn / 2.0
    if ix == 2:
        return x - loc / (sqrt(c * c - 2 * logsf) - c)
    if ix == 1:
        return x - scale * (sqrt(c * c - 2 * logsf) - c)
    raise IndexError('Index to the fixed parameter is out of bounds')


LINKS = dict(expon=_expon_link,
             weibull_min=_weibull_min_link,
             frechet_r=_weibull_min_link,
             genpareto=_genpareto_link,
             genexpon=_genexpon_link,
             gumbel_r=_gumbel_r_link,
             rayleigh=_rayleigh_link,
             trunclayleigh=_trunclayleigh_link,
             genextreme=_genextreme_link,
             exponweib=_exponweib_link,
             burr=_burr_link)


def chi2isf(p, df):
    return special.chdtri(df, p)  #pylint: disable=no-member


def chi2sf(x, df):
    return special.chdtrc(df, x)  #pylint: disable=no-member


def norm_ppf(q):
    return special.ndtri(q)


class Profile(object):
    """
    Profile Log- likelihood or Product Spacing-function for phat[i].

    Parameters
    ----------
    fit_dist : FitDistribution object
        with ML or MPS estimated distribution parameters.
    i : scalar integer
        defining which distribution parameter to keep fixed in the
        profiling process (default first non-fixed parameter)
    pmin, pmax : real scalars
        Interval for the parameter, phat[i] used in the optimization of the
        profile function (default is based on the 100*(1-alpha)% confidence
        interval computed with the delta method.)
    n : scalar integer
        Max number of points used in Lp (default 100)
    alpha : real scalar
        confidence coefficent (default 0.05)
    lmaxdiff: real scalar
        If Lmax_new-Lmax>lmaxdiff it will change the optimum parameters
        of the fitted distribution.

    Returns
    -------
    Lp : Profile log-likelihood function with parameters phat given
        the data and phat[i],  i.e.,
            Lp = max(log(f(phat| data, phat[i])))

    Member methods
    -------------
    plot() : Plot profile function with 100(1-alpha)% confidence interval
    get_bounds() : Return 100(1-alpha)% confidence interval

    Member variables
    ----------------
    fit_dist : FitDistribution data object.
    data : profile function values
    args : profile function arguments
    alpha : confidence coefficient
    Lmax : Maximum value of profile function
    alpha_cross_level :

    PROFILE is a utility function for making inferences on a particular
    component of the vector phat.
    This is usually more accurate than using the delta method assuming
    asymptotic normality of the ML estimator or the MPS estimator.

    Examples
    --------
    # MLE
    >>> import wafo.stats as ws
    >>> R = ws.weibull_min.rvs(1,size=100);
    >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

    # Better 90% CI for phat.par[i=0]
    >>> profile_phat_i = Profile(phat, i=0)
    >>> profile_phat_i.plot()
    >>> phat_ci = profile_phat_i.get_bounds(alpha=0.1)

    """
    def __init__(self, fit_dist, i=None, pmin=None, pmax=None, n=100, alpha=0.05, lmaxdiff=1e-5):

        self.fit_dist = fit_dist
        self.pmin = pmin
        self.pmax = pmax
        self.n = n
        self.alpha = alpha
        self.lmaxdiff = lmaxdiff
        self.data = None
        self.args = None

        self._set_indexes(fit_dist, i)
        method = fit_dist.method.lower()
        self._set_plot_labels(method)

        Lmax = self._loglike_max(fit_dist, method)
        self.Lmax = Lmax
        self.alpha_Lrange = 0.5 * chi2isf(self.alpha, 1)
        self.alpha_cross_level = Lmax - self.alpha_Lrange

        self._set_profile()

    def _set_plot_labels(self, method, title='', xlabel=''):
        if not title:
            title = '{:s} params'.format(self.fit_dist.dist.name)
        if not xlabel:
            xlabel = 'phat[{}]'.format(np.ravel(self.i_fixed)[0])
        percent = 100 * (1.0 - self.alpha)
        self.title = '{:g}% CI for {:s}'.format(percent, title)
        like_txt = 'likelihood' if method == 'ml' else 'product spacing'
        self.ylabel = 'Profile log' + like_txt
        self.xlabel = xlabel

    @staticmethod
    def _loglike_max(fit_dist, method):
        if method.startswith('ml'):
            return fit_dist.LLmax
        if method.startswith('mps'):
            return fit_dist.LPSmax
        raise ValueError("PROFILE is only valid for ML- or MPS- estimators")

    @staticmethod
    def _default_i_fixed(fit_dist):
        try:
            i0 = 1 - np.isfinite(fit_dist.par_fix).argmax()
        except Exception:
            i0 = 0
        return i0

    @staticmethod
    def _get_not_fixed_mask(fit_dist):
        if fit_dist.par_fix is None:
            isnotfixed = np.ones(fit_dist.par.shape, dtype=bool)
        else:
            isnotfixed = 1 - np.isfinite(fit_dist.par_fix)
        return isnotfixed

    def _check_i_fixed(self):
        if self.i_fixed not in self.i_notfixed:
            raise IndexError("Index i must be equal to an index to one of " +
                             "the free parameters.")

    def _set_indexes(self, fit_dist, i):
        if i is None:
            i = self._default_i_fixed(fit_dist)
        self.i_fixed = np.atleast_1d(i)
        isnotfixed = self._get_not_fixed_mask(fit_dist)
        self.i_notfixed = flatnonzero(isnotfixed)
        self._check_i_fixed()
        isfree = isnotfixed
        isfree[self.i_fixed] = False
        self.i_free = flatnonzero(isfree)

    def _local_link(self, fix_par, par):
        """
        Return fixed distribution parameter
        """
        penalty = 0
        num_par = len(par)
        if num_par-1 not in self.i_fixed:
            n = len(self.fit_dist.data)
            log_xmax = log(_XMAX) + 100
            scale = par[-1]
            scale_min =  _TINY/10
            penalty += 0 if scale > scale_min else n*log_xmax * np.exp(-scale)
            par[-1] = np.maximum(scale, scale_min)

        return fix_par, penalty

    def _correct_Lmax(self, Lmax, free_par, fix_par):

        if Lmax > self.Lmax + self.lmaxdiff:  # foundNewphat = True
            par_old = str(self._par)
            dL = Lmax - self.Lmax
            self.alpha_cross_level += dL
            self.Lmax = Lmax
            par = self._par.copy()
            par[self.i_free] = free_par
            par[self.i_fixed] = fix_par
            self.best_par = par
            self._par = par

            warnings.warn(
                'The fitted parameters does not provide the optimum fit. ' +
                'Something wrong with fit ' +
                '(par = {}, par_old= {}, dl = {})'.format(str(par), par_old, dL))

    def _profile_optimum(self, phatfree0, p_opt):
        # (fun, x0, args, method, jac, hess, hessp, bounds, constraints, tol, callback, options)
        with np.errstate(divide='ignore', invalid='ignore'):
            phatfree = optimize.minimize(self._profile_fun, phatfree0, args=(p_opt,),
                                         method='Nelder-Mead',
                                         options=dict(disp=0))
        Lmax = -self._profile_fun(phatfree.x, p_opt)
        self._correct_Lmax(Lmax, phatfree.x, p_opt)
        return Lmax, phatfree.x

    def _get_p_opt(self):
        return self._par[self.i_fixed]

    def _set_profile(self):
        self._par = self.fit_dist.par.copy()

        # Set up variable to profile and _local_link function
        p_opt = self._get_p_opt()

        phatfree = self._par[self.i_free].copy()

        pvec = self._get_pvec(phatfree, p_opt)

        self.data = np.full(pvec.shape, nan)
        start = (pvec >= p_opt).argmax()  # start index at optimum

        for stop, step in ((-1, -1), (len(pvec), 1)):
            phatfree = self._par[self.i_free].copy()
            for ix in range(start, stop, step):
                Lmax, phatfree = self._profile_optimum(phatfree, pvec[ix])
                self.data[ix] = Lmax
                if ix != start and Lmax < self.alpha_cross_level:
                    break
        np.putmask(pvec, np.isnan(self.data), nan)
        self.args = pvec

        self._prettify_profile()

    def _prettify_profile(self):
        pvec = self.args
        ix = flatnonzero(np.isfinite(pvec))
        self.data = self.data[ix]
        self.args = pvec[ix]
        cond = self.data == -np.inf

        if np.any(cond):
            ind, = cond.nonzero()
            self.data.put(ind, -_XMAX / 2.0)
            ind1 = np.where(ind == 0, ind, ind - 1)
            cl = self.alpha_cross_level - self.alpha_Lrange / 2.0
            try:
                t0 = ecross(self.args, self.data, ind1, cl)
                np.put(self.data, ind, cl)
                np.put(self.args, ind, t0)
            except IndexError as err:
                warnings.warn(str(err))

    def _get_variance(self):
        invfun = getattr(self, '_myinvfun', None)
        if invfun is not None:
            i_notfixed = self.i_notfixed
            pcov = self.fit_dist.par_cov[i_notfixed, :][:, i_notfixed]
            gradfun = nd.Gradient(invfun)
            phatv = self._par
            drl = gradfun(phatv[i_notfixed])
            pvar = np.sum(np.dot(drl, pcov) * drl)
            return pvar
        pvar = self.fit_dist.par_cov[self.i_fixed, :][:, self.i_fixed]
        return pvar

    def _approx_p_min_max(self, p_opt):
        pvar = self._get_variance()
        if pvar <= 1e-5 or np.isnan(pvar):
            pvar = max(abs(p_opt) * 0.5, 0.2)
        pvar = max(pvar, 0.1)
        p_crit = -norm_ppf(self.alpha / 2.0) * sqrt(np.ravel(pvar)) * 1.5
        return p_opt - p_crit * 5, p_opt + p_crit * 5

    def _p_min_max(self, phatfree0, p_opt):
        p_low, p_up = self._approx_p_min_max(p_opt)
        pmin, pmax = self.pmin, self.pmax
        if pmin is None:
            pmin = self._search_p_min_max(phatfree0, p_low, p_opt, 'min')
        if pmax is None:
            pmax = self._search_p_min_max(phatfree0, p_up, p_opt, 'max')
        return pmin, pmax

    def _adaptive_pvec(self, p_opt, pmin, pmax):
        p_crit_low = (p_opt - pmin) / 5
        p_crit_up = (pmax - p_opt) / 5
        n = int(self.n)
        n4 = n // 4
        a, b = p_opt - p_crit_low, p_opt + p_crit_up
        pvec1 = np.linspace(pmin, a, n4 + 1).ravel()
        pvec2 = np.linspace(a, b, n - 2 * n4).ravel()
        pvec3 = np.linspace(b, pmax, n4 + 1).ravel()
        pvec = np.unique(np.hstack((pvec1, p_opt, pvec2, pvec3)))
        return pvec

    def _get_pvec(self, phatfree0, p_opt):
        """ return proper interval for the variable to profile
        """
        if self.pmin is None or self.pmax is None:
            pmin, pmax = self._p_min_max(phatfree0, p_opt)
            return self._adaptive_pvec(p_opt, pmin, pmax)
        return np.linspace(self.pmin, self.pmax, self.n)

    def _update_p_opt(self, p_minmax_opt, dp, Lmax, p_minmax, j):
        # print((dp, p_minmax, p_minmax_opt, Lmax))
        converged = False
        if np.isnan(Lmax):
            dp *= 0.33
        elif Lmax < self.alpha_cross_level - self.alpha_Lrange * 5 * (j + 1):
            p_minmax_opt = p_minmax
            dp *= 0.33
        elif Lmax < self.alpha_cross_level:
            p_minmax_opt = p_minmax
            converged = True
        else:
            dp *= 1.67
        return p_minmax_opt, dp, converged

    def _search_p_min_max(self, phatfree0, p_minmax0, p_opt, direction):
        phatfree = phatfree0.copy()
        sign = dict(min=-1, max=1)[direction]
        dp = np.maximum(sign*(p_minmax0 - p_opt) / 40, 0.01) * 10
        Lmax, phatfree = self._profile_optimum(phatfree, p_opt)
        p_minmax_opt = p_minmax0
        j = 0
        converged = False
        # for j in range(51):
        while j < 51 and not converged:
            j += 1
            p_minmax = p_opt + sign * dp
            Lmax, phatfree = self._profile_optimum(phatfree, p_minmax)
            p_minmax_opt, dp, converged = self._update_p_opt(p_minmax_opt, dp,
                                                             Lmax, p_minmax, j)
        _assert_warn(j < 50, 'Exceeded max iterations. '
                     '(p_{0}0={1}, p_{0}={2}, p={3})'.format(direction,
                                                             p_minmax0,
                                                             p_minmax_opt,
                                                             p_opt))
        # print('search_pmin iterations={}'.format(j))
        return p_minmax_opt

    def _profile_fun(self, free_par, fix_par):
        """ Return negative of loglike or logps function

           free_par - vector of free parameters
           fix_par  - fixed parameter, i.e., either quantile (return level),
                      probability (return period) or distribution parameter
        """
        par = self._par.copy()
        par[self.i_free] = free_par

        par[self.i_fixed], penalty = self._local_link(fix_par, par)
        return self.fit_dist.fitfun(par) + penalty

    def _check_bounds(self, cross_level, ind, n):
        if n == 0:
            warnings.warn('Number of crossings is zero, i.e., upper and lower '
                          'bound is not found!')
            bounds = self.pmin, self.pmax
        elif n == 1:
            x0 = ecross(self.args, self.data, ind, cross_level)
            is_upcrossing = self.data[ind] < self.data[ind + 1]
            if is_upcrossing:
                bounds = x0, self.pmax
                warnings.warn('Upper bound is larger')
            else:
                bounds = self.pmin, x0
                warnings.warn('Lower bound is smaller')
        else:
            warnings.warn('Number of crossings too large! Something is wrong!')
            bounds = ecross(self.args, self.data, ind[[0, -1]], cross_level)
        return bounds

    def get_bounds(self, alpha=0.05):
        """Return confidence interval for profiled parameter
        """
        _assert_warn(self.alpha <= alpha, 'Might not be able to return bounds '
                     'with alpha less than {}'.format(self.alpha))

        cross_level = self.Lmax - 0.5 * chi2isf(alpha, 1)
        ind = findcross(self.data, cross_level)
        n = len(ind)
        if n == 2:
            bounds = ecross(self.args, self.data, ind, cross_level)
        else:
            bounds = self._check_bounds(cross_level, ind, n)
        return bounds

    def plot(self, axis=None):
        """
        Plot profile function for p_opt with 100(1-alpha)% confidence interval.
        """
        if axis is None:
            axis = plotbackend.gca()

        p_ci = self.get_bounds(self.alpha)
        axis.plot(
            self.args, self.data,
            p_ci, [self.Lmax, ] * 2, 'r--',
            p_ci, [self.alpha_cross_level, ] * 2, 'r--')
        ymax = self.Lmax + self.alpha_Lrange/10
        ymin = self.alpha_cross_level - self.alpha_Lrange/10
        axis.vlines(p_ci, ymin=ymin, ymax=self.Lmax,
                    color='r', linestyles='--')
        p_opt = self._get_p_opt()
        axis.vlines(p_opt, ymin=ymin, ymax=self.Lmax,
                    color='g', linestyles='--')
        axis.set_title(self.title)
        axis.set_ylabel(self.ylabel)
        axis.set_xlabel(self.xlabel)
        axis.set_ylim(ymin=ymin, ymax=ymax)


def plot_all_profiles(phats, plotter=None):
    def _remove_title_or_ylabel(plt, n, j):
        if j != 0:
            plt.title('')
        if j != n // 2:
            plt.ylabel('')

    def _profile(phats, k):
        profile_phat_k = Profile(phats, i=k)
        m = 0
        while hasattr(profile_phat_k, 'best_par') and m < 7:
            # iterate to find optimum phat!
            phats.fit(*profile_phat_k.best_par)
            profile_phat_k = Profile(phats, i=k)
            m += 1

        return profile_phat_k

    if plotter is None:
        plotter = plotbackend

    if phats.par_fix:
        indices = phats.i_notfixed
    else:
        indices = np.arange(len(phats.par))
    n = len(indices)
    for j, k in enumerate(indices):
        plotter.subplot(n, 1, j+1)
        profile_phat_k = _profile(phats, k)
        profile_phat_k.plot()
        _remove_title_or_ylabel(plotter, n, j)
    plotter.subplots_adjust(hspace=0.5)
    par_txt = ('{:1.2g}, '*len(phats.par))[:-2].format(*phats.par)
    plotter.suptitle('phat = [{}] (fit metod: {})'.format(par_txt, phats.method))
    return phats


class ProfileQuantile(Profile):
    """
    Profile Log- likelihood or Product Spacing-function for quantile.

    Parameters
    ----------
    fit_dist : FitDistribution object
        with ML or MPS estimated distribution parameters.
    x : real scalar
        Quantile (return value)
    i : scalar integer
        defining which distribution parameter to keep fixed in the
        profiling process (default first non-fixed parameter)
    pmin, pmax : real scalars
        Interval for the parameter, x, used in the
        optimization of the profile function (default is based on the
        100*(1-alpha)% confidence interval computed with the delta method.)
    n : scalar integer
        Max number of points used in Lp (default 100)
    alpha : real scalar
        confidence coefficent (default 0.05)
    link : function connecting the x-quantile and the survival probability
        (sf) with the fixed distribution parameter, i.e.:
        self.par[i] = link(x, logsf, self.par, i), where
            logsf = log(Prob(X>x;phat)).
        (default is fetched from the LINKS dictionary)

    Returns
    -------
    Lp : Profile log-likelihood function with parameters phat given
        the data, phat[i] and quantile (x)  i.e.,
            Lp = max(log(f(phat|data,phat(i),x)))

    Member methods
    -------------
    plot() : Plot profile function with 100(1-alpha)% confidence interval
    get_bounds() : Return 100(1-alpha)% confidence interval

    Member variables
    ----------------
    fit_dist : FitDistribution data object.
    data : profile function values
    args : profile function arguments
    alpha : confidence coefficient
    Lmax : Maximum value of profile function
    alpha_cross_level :

    ProfileQuantile is a utility function for making inferences on the
    quantile, x.
    This is usually more accurate than using the delta method assuming
    asymptotic normality of the ML estimator or the MPS estimator.

    Examples
    --------
    # MLE
    >>> import wafo.stats as ws
    >>> R = ws.weibull_min.rvs(1,size=100);
    >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

    >>> sf = 1./990
    >>> x = phat.isf(sf)

    # 80% CI for x
    >>> profile_x = ProfileQuantile(phat, x)
    >>> profile_x.plot()
    >>> x_ci = profile_x.get_bounds(alpha=0.2)
    """
    def __init__(self, fit_dist, x, i=None, pmin=None, pmax=None, n=100,
                 alpha=0.05, link=None):
        self.x = x
        self.log_sf = fit_dist.logsf(x)
        if link is None:
            link = LINKS.get(fit_dist.dist.name)
        self.link = link
        super(ProfileQuantile, self).__init__(fit_dist, i=i, pmin=pmin,
                                              pmax=pmax, n=n, alpha=alpha)

    def _get_p_opt(self):
        return self.x

    def _approx_p_min_max(self, p_opt):
        pmin, pmax = super()._approx_p_min_max(p_opt)
        a, b = self.fit_dist.support()
        return np.maximum(pmin, a), np.minimum(pmax, b)  # Make sure a < x < b

    def _p_min_max(self, phatfree0, p_opt):
        pmin, pmax = super()._p_min_max(phatfree0, p_opt)
        a, b = self.fit_dist.support()
        return np.maximum(pmin, a), np.minimum(pmax, b)  # Make sure a < x < b

    def _local_link(self, fixed_x, par):
        """
        Return fixed distribution parameter from fixed quantile
        """
        # Check if quantile outside of valid boundaries.
        fixed_x, penalty = super()._local_link(fixed_x, par)
        fix_par = self.link(fixed_x, self.log_sf, par, self.i_fixed)
        return fix_par, penalty

    def _myinvfun(self, phatnotfixed):
        mphat = self._par.copy()
        mphat[self.i_notfixed] = phatnotfixed
        prb = exp(self.log_sf)
        return self.fit_dist.dist.isf(prb, *mphat)

    def _set_plot_labels(self, method, title='', xlabel='x'):
        if not title:
            title = '{:s} quantile'.format(self.fit_dist.dist.name)
        super(ProfileQuantile, self)._set_plot_labels(method, title, xlabel)


class ProfileProbability(Profile):
    """Profile Log- likelihood or Product Spacing-function probability.

    Parameters
    ----------
    fit_dist : FitDistribution object
        with ML or MPS estimated distribution parameters.
    logsf : real scalar
        logarithm of survival probability
    i : scalar integer
        defining which distribution parameter to keep fixed in the
        profiling process (default first non-fixed parameter)
    pmin, pmax : real scalars
        Interval for the parameter, log_sf, used in the
        optimization of the profile function (default is based on the
        100*(1-alpha)% confidence interval computed with the delta method.)
    n : scalar integer
        Max number of points used in Lp (default 100)
    alpha : real scalar
        confidence coefficent (default 0.05)
    link : function connecting the x-quantile and the survival probability
        (sf) with the fixed distribution parameter, i.e.:
        self.par[i] = link(x, logsf, self.par, i), where
            logsf = log(Prob(X>x;phat)).
        (default is fetched from the LINKS dictionary)

    Returns
    -------
    Lp : Profile log-likelihood function with parameters phat given
        the data, phat[i] and quantile (x)  i.e.,
            Lp = max(log(f(phat|data,phat(i),x)))

    Member methods
    -------------
    plot() : Plot profile function with 100(1-alpha)% confidence interval
    get_bounds() : Return 100(1-alpha)% confidence interval

    Member variables
    ----------------
    fit_dist : FitDistribution data object.
    data : profile function values
    args : profile function arguments
    alpha : confidence coefficient
    Lmax : Maximum value of profile function
    alpha_cross_level :

    ProfileProbability is a utility function for making inferences the survival
    probability (sf).
    This is usually more accurate than using the delta method assuming
    asymptotic normality of the ML estimator or the MPS estimator.

    Examples
    --------
    # MLE
    >>> import wafo.stats as ws
    >>> R = ws.weibull_min.rvs(1,size=100);
    >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

    >>> sf = 1./990

    # 80% CI for sf
    >>> profile_logsf = ProfileProbability(phat, np.log(sf))
    >>> profile_logsf.plot()
    >>> logsf_ci = profile_logsf.get_bounds(alpha=0.2)
    """
    def __init__(self, fit_dist, logsf, i=None, pmin=None, pmax=None, n=100,
                 alpha=0.05, link=None):
        self.x = fit_dist.isf(np.exp(logsf))
        self.log_sf = logsf
        if link is None:
            link = LINKS.get(fit_dist.dist.name)
        self.link = link
        super(ProfileProbability, self).__init__(fit_dist, i=i, pmin=pmin,
                                                 pmax=pmax, n=n, alpha=alpha)

    def _get_p_opt(self):
        return self.log_sf

    def _approx_p_min_max(self, p_opt):
        pmin, pmax = super()._approx_p_min_max(p_opt)
        return pmin, np.minimum(pmax, -_TINY)  # Make sure logsf < 0

    def _p_min_max(self, phatfree0, p_opt):
        pmin, pmax = super()._p_min_max(phatfree0, p_opt)
        return pmin, np.minimum(pmax, -_TINY)  # Make sure logsf<0

    def _local_link(self, fixed_log_sf, par):
        """
        Return fixed distribution parameter from fixed log_sf
        """
        fixed_log_sf, penalty = super()._local_link(fixed_log_sf, par)
        fix_par = self.link(self.x, fixed_log_sf, par, self.i_fixed)
        return fix_par, penalty

    def _myinvfun(self, phatnotfixed):
        """_myprbfun"""
        mphat = self._par.copy()
        mphat[self.i_notfixed] = phatnotfixed
        logsf = self.fit_dist.dist.logsf(self.x, *mphat)
        return np.where(np.isfinite(logsf), logsf, np.nan)

    def _set_plot_labels(self, method, title='', xlabel=''):
        if not title:
            title = '{:s} probability'.format(self.fit_dist.dist.name)
        if not xlabel:
            xlabel = 'log(sf)'
        super(ProfileProbability, self)._set_plot_labels(method, title, xlabel)



def _set_rv_frozen_docstrings(klass):
    def _get_text2remove(doc):
        """Returns text containing description of distribution parameter arguments."""
        start = doc.lower().find('arg1, arg2, arg3')

        stop = doc.lower().find('scale parameter (default=1)')
        if stop > 0:
            stop += 28
        rmtxt = doc[start:stop].rstrip()
        return rmtxt

    cnames = ["pdf", "cdf", "ppf", "rvs", "isf", "sf", "logcdf", "logpdf",
              "stats", "mean", "median", "var", "std", "moment", "entropy",
              "interval", "expect", "support"]
    dnames = ["pmf", "logpmf"]

    def _get_rv_frozen_docstrings():
        """Returns doc
        """
        docstrings = {}
        for name in cnames:
            try:
                doc = getattr(ss.beta, name).__doc__
                rmtxt = _get_text2remove(doc)
                # remove description of distribution parameter arguments as well as
                # any given examples:
                docstrings[name] = doc.replace(rmtxt,'').partition("Examples\n")[0]
            except AttributeError as error:
                warnings.warn(str(error))

        for name in dnames:
            try:
                doc = getattr(ss.binom, name).__doc__
                rmtxt = _get_text2remove(doc)
                docstrings[name] = doc.replace(rmtxt,'')
            except AttributeError as error:
                warnings.warn(str(error))

        return docstrings

    docstrings = _get_rv_frozen_docstrings()

    for name in cnames+dnames:
        method = getattr(klass, name, None)
        if method is not None:
            method.__doc__ = docstrings[name]


class rv_frozen(_rv_frozen):
    """Frozen continous or discrete 1D Random Variable object (RV)

    Methods
    -------
    rvs(size=1)
        Random variates.
    pdf(x)
        Probability density function.
    cdf(x)
        Cumulative density function.
    sf(x)
        Survival function (1-cdf --- sometimes more accurate).
    ppf(q)
        Percent point function (inverse of cdf --- percentiles).
    isf(q)
        Inverse survival function (inverse of sf).
    stats(moments='mv')
        Mean('m'), variance('v'), skew('s'), and/or kurtosis('k').
    moment(n)
        n-th order non-central moment of distribution.
    entropy()
        (Differential) entropy of the RV.
    interval(alpha)
        Confidence interval with equal areas around the median.
    expect(func, lb, ub, conditional=False)
        Calculate expected value of a function with respect to the
        distribution.
    """
_set_rv_frozen_docstrings(rv_frozen)



class FitDistribution(rv_frozen):
    """
    Return estimators to shape, location, and scale from data

    Starting points for the fit are given by input arguments.  For any
    arguments not given starting points, dist._fitstart(data) is called
    to get the starting estimates.

    You can hold some parameters fixed to specific values by passing in
    keyword arguments f0..fn for shape paramters and floc, fscale for
    location and scale parameters.

    Parameters
    ----------
    dist : scipy distribution object
        distribution to fit to data
    data : array-like
        Data to use in calculating the ML or MPS estimators
    args : optional
        Starting values for any shape arguments (those not specified
        will be determined by dist._fitstart(data))
    kwds : loc, scale
        Starting values for the location and scale parameters
        Special keyword arguments are recognized as holding certain
        parameters fixed:
            f0..fn : hold respective shape paramters fixed
            floc : hold location parameter fixed to specified value
            fscale : hold scale parameter fixed to specified value
        method : of estimation. Options are
            'ml' : Maximum Likelihood method (default)
            'mps': Maximum Product Spacing method
        alpha : scalar, optional
            Confidence coefficent  (default=0.05)
        search : bool
            If true search for best estimator (default),
            otherwise return object with initial distribution parameters
        copydata : bool
            If true copydata (default)
        optimizer : The optimizer to use.  The optimizer must take func,
                     and starting position as the first two arguments,
                     plus args (for extra arguments to pass to the
                     function to be optimized) and disp=0 to suppress
                     output as keyword arguments.

    Return
    ------
    phat : FitDistribution object
        Fitted distribution object with following member variables:
        LLmax  : loglikelihood function evaluated using par
        LPSmax : log product spacing function evaluated using par
        pvalue : p-value for the fit
        par : distribution parameters (fixed and fitted)
        par_cov : covariance of distribution parameters
        par_fix : fixed distribution parameters
        par_lower : lower (1-alpha)% confidence bound for the parameters
        par_upper : upper (1-alpha)% confidence bound for the parameters

    Note
    ----
    `data` is sorted using this function, so if `copydata`==False the data
    in your namespace will be sorted as well.

    Examples
    --------
    Estimate distribution parameters for weibull_min distribution.
    >>> import wafo.stats as ws
    >>> R = ws.weibull_min.rvs(1,size=100);
    >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

    or alternatively:
    >>> phat = ws.weibull_min.fit2(R, 1, scale=1, floc=0.0)

    # Plot various diagnostic plots to asses quality of fit.
    >>> phat.plotfitsummary()

    # phat.par holds the estimated parameters
    # phat.par_upper upper CI for parameters
    # phat.par_lower lower CI for parameters

    # Better 90% CI for phat.par[0]
    >>> profile_phat_i = phat.profile(i=0)
    >>> profile_phat_i.plot()
    >>> p_ci = profile_phat_i.get_bounds(alpha=0.1)

    >>> sf = 1./990
    >>> x = phat.isf(sf)

    # 80% CI for x
    >>> profile_x = phat.profile_quantile(x=x)
    >>> profile_x.plot()
    >>> x_ci = profile_x.get_bounds(alpha=0.2)

     # 80% CI for logsf=log(sf)
    >>> profile_logsf = phat.profile_probability(log(sf))
    >>> profile_logsf.plot()
    >>> sf_ci = profile_logsf.get_bounds(alpha=0.2)
    """

    def __init__(self, dist, data, args=(), **kwds):
        extradoc = """
    plotfitsummary()
         Plot various diagnostic plots to asses quality of fit.
    plotecdf()
        Plot Empirical and fitted Cumulative Distribution Function
    plotesf()
        Plot Empirical and fitted Survival Function
    plotepdf()
        Plot Empirical and fitted Probability Distribution Function
    plotresq()
        Displays a residual quantile plot.
    plotresprb()
        Displays a residual probability plot.
    profile()
        Return Profile Log- likelihood or Product Spacing-function.

       """
#    Member variables
#    ----------------
#    data - data used in fitting
#    alpha - confidence coefficient
#    method - method used
#    LLmax  - loglikelihood function evaluated using par
#    LPSmax - log product spacing function evaluated using par
#    pvalue - p-value for the fit
#    search - True if search for distribution parameters (default)
#    copydata - True if copy input data (default)
#
#    par     - parameters (fixed and fitted)
#    par_cov - covariance of parameters
#    par_fix - fixed parameters
#    par_lower - lower (1-alpha)% confidence bound for the parameters
#    par_upper - upper (1-alpha)% confidence bound for the parameters
#
#        """
        self.__doc__ = str(rv_frozen.__doc__) + extradoc
        self.dist = dist
        self.par_fix = None
        self.alpha = kwds.pop('alpha', 0.05)
        self.copydata = kwds.pop('copydata', True)
        self.method = kwds.get('method', 'ml')
        self.search = kwds.get('search', True)
        self.data = np.ravel(data)
        if self.copydata:
            self.data = self.data.copy()
        self.data.sort()
        if isinstance(args, (float, int)):
            args = (args, )
        self.fit(*args, **kwds)

    @property
    def par(self):
        return self._par

    @par.setter
    def par(self, par):
        par = asarray(par)
        super(FitDistribution, self).__init__(self.dist, *par.tolist())
        self._par = par

    def _set_fixed_par(self, fixedn):
        self.par_fix = [nan] * len(self.par)
        for i in fixedn:
            self.par_fix[i] = self.par[i]
        self.i_notfixed = flatnonzero(1 - isfinite(self.par_fix))
        self.i_fixed = flatnonzero(isfinite(self.par_fix))


    def fit(self, *args, **kwds):
        par, fixedn = self.dist._fit(self.data, *args, **kwds.copy())

        self.par = par
        somefixed = len(fixedn) > 0
        if somefixed:
            self._set_fixed_par(fixedn)

        self.par_cov = self._compute_cov()

        # Set confidence interval for parameters
        pvar = np.diag(self.par_cov)
        zcrit = -norm_ppf(self.alpha / 2.0)
        self.par_lower = self.par - zcrit * sqrt(pvar)
        self.par_upper = self.par + zcrit * sqrt(pvar)

        self.LLmax = -self._nnlf(self.par, self.data)
        self.LPSmax = -self._nlogps(self.par, self.data)
        self.pvalue = self._pvalue(self.par, self.data,
                                   unknown_numpar=len(par)-len(fixedn))

    @property
    def method(self):
        return self._method

    @method.setter
    def method(self, method):
        self._method = method.lower()
        if self._method.startswith('mps'):
            self._fitfun = self._nlogps
        else:
            self._fitfun = self._nnlf

    def __repr__(self):
        params = ['alpha', 'method', 'LLmax', 'LPSmax', 'pvalue',
                  'par', 'par_lower', 'par_upper', 'par_fix', 'par_cov']
        t = ['%s:\n' % self.__class__.__name__]
        for par in params:
            t.append('%s = %s\n' % (par, str(getattr(self, par))))
        return ''.join(t)

    @staticmethod
    def _hessian(nnlf, theta, data, eps=None):
        """ approximate hessian of nnlf where theta are the parameters
        (including loc and scale)
        """
        if eps is None:
            eps = (_EPS) ** 0.25
        num_par = len(theta)
        # pab 07.01.2001: Always choose the stepsize h so that
        # it is an exactly representable number.
        # This is important when calculating numerical derivatives and is
        #  accomplished by the following.
        delta = (eps + 2.0) - 2.0
        delta2 = delta ** 2.0
        # Approximate 1/(nE( (d L(x|theta)/dtheta)^2)) with
        #              1/(d^2 L(theta|x)/dtheta^2)
        # using central differences

        LL = nnlf(theta, data)
        H = zeros((num_par, num_par))   # Hessian matrix
        theta = tuple(theta)
        for ix in range(num_par):
            sparam = list(theta)
            sparam[ix] = theta[ix] + delta
            fp = nnlf(sparam, data)

            sparam[ix] = theta[ix] - delta
            fm = nnlf(sparam, data)

            H[ix, ix] = (fp - 2 * LL + fm) / delta2
            for iy in range(ix + 1, num_par):
                sparam[ix] = theta[ix] + delta
                sparam[iy] = theta[iy] + delta
                fpp = nnlf(sparam, data)

                sparam[iy] = theta[iy] - delta
                fpm = nnlf(sparam, data)

                sparam[ix] = theta[ix] - delta
                fmm = nnlf(sparam, data)

                sparam[iy] = theta[iy] + delta
                fmp = nnlf(sparam, data)

                H[ix, iy] = ((fpp + fmm) - (fmp + fpm)) / (4. * delta2)
                H[iy, ix] = H[ix, iy]
                sparam[iy] = theta[iy]
        return -H

    def _nnlf(self, theta, x):
        return self.dist._penalized_nnlf(theta, x)

    def _nlogps(self, theta, x):
        """ Moran's negative log Product Spacings statistic

            where theta are the parameters (including loc and scale)

            Note the data in x must be sorted

        References
        -----------

        R. C. H. Cheng; N. A. K. Amin (1983)
        "Estimating Parameters in Continuous Univariate Distributions with a
        Shifted Origin.",
        Journal of the Royal Statistical Society. Series B (Methodological),
        Vol. 45, No. 3. (1983), pp. 394-403.

        R. C. H. Cheng; M. A. Stephens (1989)
        "A Goodness-Of-Fit Test Using Moran's Statistic with Estimated
        Parameters", Biometrika, 76, 2, pp 385-392

        Wong, T.S.T. and Li, W.K. (2006)
        "A note on the estimation of extreme value distributions using maximum
        product of spacings.",
        IMS Lecture Notes Monograph Series 2006, Vol. 52, pp. 272-283
        """
        return self.dist._penalized_nlogps(theta, x)

    def _invert_hessian(self, H):
        # pylint: disable=invalid-unary-operand-type
        par_cov = zeros(H.shape)
        somefixed = ((self.par_fix is not None) and np.any(isfinite(self.par_fix)))
        if somefixed:
            allfixed = np.all(isfinite(self.par_fix))
            if not allfixed:
                pcov = -pinv2(H[self.i_notfixed, :][..., self.i_notfixed])
                for row, ix in enumerate(list(self.i_notfixed)):
                    par_cov[ix, self.i_notfixed] = pcov[row, :]
        else:
            par_cov = -pinv2(H)
        return par_cov

    def _compute_cov(self):
        """Compute covariance
        """

        H = np.asmatrix(self._hessian(self._fitfun, self.par, self.data))
        # H = -nd.Hessian(lambda par: self._fitfun(par, self.data),
        #                 method='forward')(self.par)
        self.H = H

        try:
            par_cov = self._invert_hessian(H)
        except:
            par_cov = nan * np.ones(H.shape)
        return par_cov

    def fitfun(self, phat):
        return self._fitfun(phat, self.data)

    def profile(self, **kwds):
        """
        Profile Log- likelihood or Log Product Spacing- function for phat[i]

        Examples
        --------
        # MLE
        >>> import wafo.stats as ws
        >>> R = ws.weibull_min.rvs(1,size=100);
        >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

        # Better CI for phat.par[i=0]
        >>> Lp = phat.profile(i=0)
        >>> Lp.plot()
        >>> phat_ci = Lp.get_bounds(alpha=0.1)

        See also
        --------
        Profile
        """
        return Profile(self, **kwds)

    def profile_quantile(self, x, **kwds):
        """
        Profile Log- likelihood or Product Spacing-function for quantile.

        Examples
        --------
        # MLE
        >>> import wafo.stats as ws
        >>> R = ws.weibull_min.rvs(1,size=100);
        >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

        >>> sf = 1./990
        >>> x = phat.isf(sf)

        # 80% CI for x
        >>> profile_x = phat.profile_quantile(x)
        >>> profile_x.plot()
        >>> x_ci = profile_x.get_bounds(alpha=0.2)
        """
        return ProfileQuantile(self, x, **kwds)

    def profile_probability(self, log_sf, **kwds):
        """
        Profile Log- likelihood or Product Spacing-function for probability.

        Examples
        --------
        # MLE
        >>> import wafo.stats as ws
        >>> R = ws.weibull_min.rvs(1,size=100);
        >>> phat = FitDistribution(ws.weibull_min, R, 1, scale=1, floc=0.0)

        >>> log_sf = np.log(1./990)

        # 80% CI for log_sf
        >>> profile_logsf = phat.profile_probability(log_sf)
        >>> profile_logsf.plot()
        >>> log_sf_ci = profile_logsf.get_bounds(alpha=0.2)
        """
        return ProfileProbability(self, log_sf, **kwds)

    def ci_sf(self, sf, alpha=0.05, i=2):
        ci = []
        for log_sfi in np.atleast_1d(np.log(sf)).ravel():
            try:
                Lp = self.profile_probability(log_sfi, i=i)
                ci.append(np.exp(Lp.get_bounds(alpha=alpha)))
            except Exception:
                ci.append((np.nan, np.nan))
        return np.array(ci)

    def ci_quantile(self, x, alpha=0.05, i=2):
        ci = []
        for xi in np.atleast_1d(x).ravel():
            try:
                Lx = self.profile_quantile(xi, i=i)
                ci.append(Lx.get_bounds(alpha=alpha))
            except Exception:
                ci.append((np.nan, np.nan))
        return np.array(ci)

    def _fit_summary_text(self):
        fixstr = ''
        if self.par_fix is not None:
            numfix = len(self.i_fixed)
            if numfix > 0:
                format0 = ', '.join(['%d'] * numfix)
                format1 = ', '.join(['%g'] * numfix)
                phatistr = format0 % tuple(self.i_fixed)
                phatvstr = format1 % tuple(self.par[self.i_fixed])
                fixstr = 'Fixed: phat[{0:s}] = {1:s} '.format(phatistr,
                                                              phatvstr)
        subtxt = ('Fit method: {0:s}, Fit p-value: {1:2.2f} {2:s}, ' +
                  'phat=[{3:s}], {4:s}')
        par_txt = ('{:1.2g}, ' * len(self.par))[:-2].format(*self.par)
        try:
            LL_txt = 'Lps_max={:2.2g}, Ll_max={:2.2g}'.format(self.LPSmax,
                                                              self.LLmax)
        except Exception:
            LL_txt = 'Lps_max={}, Ll_max={}'.format(self.LPSmax, self.LLmax)
        txt = subtxt.format(self.method.upper(), self.pvalue, fixstr, par_txt,
                            LL_txt)
        return txt

    def plotfitsummary(self, axes=None, fig=None, plotter=None):
        """ Plot various diagnostic plots to asses the quality of the fit.

        PLOTFITSUMMARY displays probability plot, density plot, residual
        quantile plot and residual probability plot.
        The purpose of these plots is to graphically assess whether the data
        could come from the fitted distribution. If so the empirical- CDF and
        PDF should follow the model and the residual plots will be linear.
        Other distribution types will introduce curvature in the residual
        plots.
        """
        if plotter is None:
            plotter = plotbackend
        if axes is None:
            fig, axes = plotter.subplots(2, 2, figsize=(11, 8))
            fig.subplots_adjust(hspace=0.4, wspace=0.4)
            # plt.subplots_adjust(hspace=0.4, wspace=0.4)
        # self.plotecdf()
        plot_funs = (self.plotesf, self.plotepdf,
                     self.plotresq, self.plotresprb)
        for axis, plot in zip(axes.ravel(), plot_funs):
            plot(axis=axis)

        if fig is None:
            fig = plotter.gcf()
        try:
            txt = self._fit_summary_text()
            fig.text(0.05, 0.01, txt)
        except AttributeError:
            pass

    def plotesf(self, symb1='r-', symb2='b.', axis=None, plot_ci=False, plotter=None):
        """  Plot Empirical and fitted Survival Function

        The purpose of the plot is to graphically assess whether
        the data could come from the fitted distribution.
        If so the empirical CDF should resemble the model CDF.
        Other distribution types will introduce deviations in the plot.
        """
        if plotter is None:
            plotter = plotbackend
        if axis is None:
            axis = plotter.gca()
        n = len(self.data)
        sf = arange(n, 0, -1) / (n+1)
        axis.semilogy(self.data, sf, symb2,
                      self.data, self.sf(self.data), symb1)

        if plot_ci:
            low = int(np.log10(1.0/n)-0.7) - 1
            sf1 = np.logspace(low, -0.5, 7)[::-1]
            ci1 = self.ci_sf(sf1, alpha=0.05, i=2)
            axis.semilogy(self.isf(sf1), ci1, 'r--')
        axis.set_xlabel('x')
        axis.set_ylabel('F(x) (%s)' % self.dist.name)
        axis.set_title('Empirical SF plot')

    def plotecdf(self, symb1='r-', symb2='b.', axis=None, plotter=None):
        """  Plot Empirical and fitted Cumulative Distribution Function

        The purpose of the plot is to graphically assess whether
        the data could come from the fitted distribution.
        If so the empirical CDF should resemble the model CDF.
        Other distribution types will introduce deviations in the plot.
        """
        if plotter is None:
            plotter = plotbackend
        if axis is None:
            axis = plotter.gca()
        n = len(self.data)
        ecdf = arange(1, n + 1) / (n+1)
        axis.plot(self.data, ecdf, symb2,
                  self.data, self.cdf(self.data), symb1)
        axis.set_xlabel('x')
        axis.set_ylabel('F(x) ({})'.format(self.dist.name))
        axis.set_title('Empirical CDF plot')

    def _get_grid(self, odd=False):
        x = np.atleast_1d(self.data)
        n = np.ceil(4 * np.sqrt(np.sqrt(len(x))))
        mn = x.min()
        mx = x.max()
        d = (mx - mn) / n * 2
        e = np.floor(np.log(d) / np.log(10))
        m = np.floor(d / 10 ** e)
        if m > 5:
            m = 5
        elif m > 2:
            m = 2
        d = m * 10 ** e
        mn = (np.floor(mn / d) - 1) * d - odd * d / 2
        mx = (np.ceil(mx / d) + 1) * d + odd * d / 2
        limits = np.arange(mn, mx, d)
        return limits

    @staticmethod
    def _staircase(x, y):
        xx = x.reshape(-1, 1).repeat(3, axis=1).ravel()[1:-1]
        yy = y.reshape(-1, 1).repeat(3, axis=1)
        # yy[0,0] = 0.0 # pdf
        yy[:, 0] = 0.0  # histogram
        yy.shape = (-1,)
        yy = np.hstack((yy, 0.0))
        return xx, yy

    def _get_empirical_pdf(self):
        limits = self._get_grid()
        pdf, x = np.histogram(self.data, bins=limits, density=True)
        return self._staircase(x, pdf)

    def plotepdf(self, symb1='r-', symb2='b-', axis=None, plotter=None):
        """Plot Empirical and fitted Probability Density Function

        The purpose of the plot is to graphically assess whether
        the data could come from the fitted distribution.
        If so the histogram should resemble the model density.
        Other distribution types will introduce deviations in the plot.
        """
        if plotter is None:
            plotter = plotbackend
        if axis is None:
            axis = plotter.gca()
        x, pdf = self._get_empirical_pdf()
        ymax = pdf.max()
        # axis.hist(self.data,normed=True,fill=False)
        axis.plot(self.data, self.pdf(self.data), symb1,
                  x, pdf, symb2)
        axis1 = list(axis.axis())
        axis1[3] = min(ymax * 1.3, axis1[3])
        axis.axis(axis1)
        axis.set_xlabel('x')
        axis.set_ylabel('f(x) (%s)' % self.dist.name)
        axis.set_title('Density plot')

    def plotresq(self, symb1='r-', symb2='b.', axis=None, plotter=None):
        """PLOTRESQ displays a residual quantile plot.

        The purpose of the plot is to graphically assess whether
        the data could come from the fitted distribution. If so the
        plot will be linear. Other distribution types will introduce
        curvature in the plot.
        """
        if plotter is None:
            plotter = plotbackend
        if axis is None:
            axis = plotter.gca()
        n = len(self.data)
        eprob = arange(1, n + 1) / (n + 1)
        y = self.ppf(eprob)
        y1 = self.data[[0, -1]]
        axis.plot(self.data, y, symb2, y1, y1, symb1)
        axis.set_xlabel('Empirical')
        axis.set_ylabel('Model (%s)' % self.dist.name)
        axis.set_title('Residual Quantile Plot')
        axis.axis('tight')
        axis.axis('equal')

    def plotresprb(self, symb1='r-', symb2='b.', axis=None, plotter=None):
        """ PLOTRESPRB displays a residual probability plot.

        The purpose of the plot is to graphically assess whether
        the data could come from the fitted distribution. If so the
        plot will be linear. Other distribution types will introduce curvature
        in the plot.
        """
        if plotter is None:
            plotter = plotbackend
        if axis is None:
            axis = plotter.gca()
        n = len(self.data)

        ecdf = arange(1, n + 1) / (n + 1)
        mcdf = self.cdf(self.data)
        p1 = [0, 1]
        axis.plot(ecdf, mcdf, symb2, p1, p1, symb1)
        axis.set_xlabel('Empirical')
        axis.set_ylabel('Model (%s)' % self.dist.name)
        axis.set_title('Residual Probability Plot')
        axis.axis('equal')
        axis.axis([0, 1, 0, 1])

    def _pvalue(self, theta, x, unknown_numpar=None):
        """ Return P-value for the fit using Moran's negative log Product
        Spacings statistic

            where theta are the parameters (including loc and scale)

            Note: the data in x must be sorted
        """
        dx = np.diff(x, axis=0)
        tie = (dx == 0)
        if np.any(tie):
            warnings.warn(
                'P-value is on the conservative side (i.e. too large) due to' +
                ' ties in the data!')

        T = self._nlogps(theta, x)

        n = len(x)
        np1 = n + 1
        if unknown_numpar is None:
            k = len(theta)
        else:
            k = unknown_numpar

        is_par_unknown = True
        m = (np1) * (log(np1) + 0.57722) - 0.5 - 1.0 / (12. * (np1))
        v = (np1) * (pi ** 2. / 6.0 - 1.0) - 0.5 - 1.0 / (6. * (np1))
        C1 = m - sqrt(0.5 * n * v)
        C2 = sqrt(v / (2.0 * n))
        # chi2 with n degrees of freedom
        Tn = (T + 0.5 * k * is_par_unknown - C1) / C2
        pvalue = chi2sf(Tn, n)  # _WAFODIST.chi2.sf(Tn, n)
        return pvalue


def test_doctstrings():
    import doctest
    doctest.testmod()


def test1():
    import wafo.stats as ws
    dist = ws.weibull_min
    plt = plotbackend
    # dist = ws.bradford
    # dist = ws.gengamma
    R = dist.rvs(2, .5, size=500)
    phat = FitDistribution(dist, R, floc=0.5, method='ml')
    phats = FitDistribution(dist, R, floc=0.5, method='mps')
    # import matplotlib.pyplot as plt
    plt.figure(0)
    plot_all_profiles(phat, plot=plt)

    plt.figure(1)
    phats.plotfitsummary()

#    plt.figure(2)
#    plot_all_profiles(phat, plot=plt)


#    plt.figure(3)
#    phat.plotfitsummary()

    plt.figure(4)

    sf = 1./990
    x = phat.isf(sf)

    # 80% CI for x
    profile_x = ProfileQuantile(phat, x)
    profile_x.plot()
    # x_ci = profile_x.get_bounds(alpha=0.2)

    plt.figure(5)

    sf = 1./990
    x = phat.isf(sf)

    # 80% CI for x
    profile_logsf = ProfileProbability(phat, np.log(sf))
    profile_logsf.plot()
    # logsf_ci = profile_logsf.get_bounds(alpha=0.2)
    plt.show('hold')


if __name__ == '__main__':
    # test1()
    test_doctstrings()
